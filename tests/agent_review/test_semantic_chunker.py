from __future__ import annotations

import socket

from app.agent_review.semantic_chunker import (
    build_semantic_chunk_plan,
    classify_file,
    extract_files_from_intake,
)


def _intake(files: list[object] | None = None, *, status: str = "complete") -> dict[str, object]:
    artifacts: dict[str, object] = {
        "file-diff-context.json": {
            "name": "file-diff-context.json",
            "path": "file-diff-context.json",
            "kind": "json",
            "content": {"files": files if files is not None else []},
        },
        "checks.json": {
            "name": "checks.json",
            "path": "checks.json",
            "kind": "json",
            "content": {"status": "ok"},
        },
        "local-code-intelligence.json": {
            "name": "local-code-intelligence.json",
            "path": "local-code-intelligence.json",
            "kind": "json",
            "content": {"signals": []},
        },
    }
    return {
        "schema_version": "agent-review.intake.v1",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {"domain_contracts": {"rules": []}},
        "artifacts": artifacts,
        "artifact_status": [
            {"name": "file-diff-context.json", "available": True, "valid": True, "status": "available"},
            {"name": "checks.json", "available": True, "valid": True, "status": "available"},
            {"name": "local-code-intelligence.json", "available": True, "valid": True, "status": "available"},
        ],
        "status": status,
        "limitations": [],
    }


def _groups(plan) -> dict[str, list[str]]:  # noqa: ANN001
    return {chunk.semantic_group: chunk.files for chunk in plan.chunks}


def test_semantic_chunker_groups_backend_api_schema_files() -> None:
    plan = build_semantic_chunk_plan(
        _intake([
            "backend/api/notification_admin.py",
            "backend/services/notification_event_projection.py",
            "backend/models/user.py",
            "app/api/routes.py",
            "app/services/schedule_service.py",
            "app/schedule.py",
        ])
    )

    groups = _groups(plan)
    assert groups["api_schema_contract"] == ["backend/api/notification_admin.py", "app/api/routes.py"]
    assert groups["primary_backend_logic"] == [
        "backend/services/notification_event_projection.py",
        "backend/models/user.py",
        "app/services/schedule_service.py",
        "app/schedule.py",
    ]


def test_semantic_chunker_groups_frontend_files() -> None:
    plan = build_semantic_chunk_plan(_intake(["frontend/src/pages/admin_notifications_page.jsx"]))

    assert _groups(plan)["frontend_ui"] == ["frontend/src/pages/admin_notifications_page.jsx"]


def test_semantic_chunker_groups_tests_files() -> None:
    plan = build_semantic_chunk_plan(_intake(["frontend/tests/admin_notifications_page.test.jsx"]))

    assert _groups(plan)["tests"] == ["frontend/tests/admin_notifications_page.test.jsx"]


def test_semantic_chunker_groups_docs_changelog_files() -> None:
    plan = build_semantic_chunk_plan(_intake(["docs/AGENT_REVIEW_ENGINE.md", "CHANGELOG.md"]))

    assert _groups(plan)["docs_changelog"] == ["docs/AGENT_REVIEW_ENGINE.md", "CHANGELOG.md"]


def test_semantic_chunker_groups_workflow_aiops_files() -> None:
    plan = build_semantic_chunk_plan(
        _intake([".github/workflows/agent-review.yml", "scripts/build-aiops-review-bundle.py"])
    )

    assert _groups(plan)["workflow_aiops"] == [
        ".github/workflows/agent-review.yml",
        "scripts/build-aiops-review-bundle.py",
    ]


def test_semantic_chunker_marks_unknown_files() -> None:
    assert classify_file("unknown/path/weird.file") == "unknown"


def test_semantic_chunker_prioritizes_suspicious_out_of_scope() -> None:
    plan = build_semantic_chunk_plan(
        _intake(["backend/api/notification_admin.py", "deploy/prod.env"]),
        max_blocks=6,
    )

    assert plan.chunks[0].semantic_group == "suspicious_out_of_scope"
    assert plan.chunks[0].files == ["deploy/prod.env"]


def test_semantic_chunker_respects_max_blocks_six() -> None:
    plan = build_semantic_chunk_plan(
        _intake(
            [
                "prod.env",
                "backend/api/notification_admin.py",
                "backend/services/notification_event_projection.py",
                ".github/workflows/agent-review.yml",
                "frontend/src/pages/admin_notifications_page.jsx",
                "frontend/tests/admin_notifications_page.test.jsx",
                "docs/AGENT_REVIEW_ENGINE.md",
                "unknown/path/weird.file",
            ]
        ),
        max_blocks=6,
    )

    assert len(plan.chunks) == 6
    assert "unknown/path/weird.file" in plan.files_not_covered
    assert "max_blocks_exceeded" in plan.limitations


def test_semantic_chunker_does_not_drop_files_silently() -> None:
    files = [
        "prod.env",
        "backend/api/notification_admin.py",
        "backend/services/notification_event_projection.py",
        ".github/workflows/agent-review.yml",
        "frontend/src/pages/admin_notifications_page.jsx",
        "frontend/tests/admin_notifications_page.test.jsx",
        "docs/AGENT_REVIEW_ENGINE.md",
        "unknown/path/weird.file",
    ]
    plan = build_semantic_chunk_plan(_intake(files), max_blocks=6)

    accounted = set(plan.files_covered + plan.files_partially_covered + plan.files_not_covered)
    assert accounted == set(files)


def test_semantic_chunker_degraded_when_no_file_context() -> None:
    plan = build_semantic_chunk_plan(_intake([]))

    assert plan.status == "degraded"
    assert plan.chunks == []
    assert "file_context_missing" in plan.limitations


def test_semantic_chunker_uses_sanitized_intake_only(monkeypatch) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setattr(socket, "socket", fail_network)
    plan = build_semantic_chunk_plan(
        _intake([{"path": "backend/services/token=SUPERSECRET.py"}])
    )

    rendered = plan.model_dump_json()
    assert "SUPERSECRET" not in rendered
    assert "[REDACTED]" in rendered


def test_semantic_chunker_accepts_current_phase1_intake_schema_with_limitation() -> None:
    plan = build_semantic_chunk_plan(_intake(["backend/api/notification_admin.py"]))

    assert plan.status == "partial"
    assert "intake_schema_id_missing" in plan.limitations


def test_semantic_chunker_accepts_files_dict_keys() -> None:
    files, limitations = extract_files_from_intake(
        _intake(
            [
                {"path": "backend/api/a.py"},
                {"file": "backend/services/b.py"},
                {"filename": "frontend/src/c.jsx"},
                {"name": "docs/d.md"},
            ]
        )
    )

    assert limitations == []
    assert files == ["backend/api/a.py", "backend/services/b.py", "frontend/src/c.jsx", "docs/d.md"]


def test_semantic_chunker_marks_budget_overflow_partial() -> None:
    plan = build_semantic_chunk_plan(
        _intake(["backend/services/notification_event_projection.py", "backend/services/another_projection.py"]),
        max_chars_per_block=700,
    )

    assert plan.status == "partial"
    assert plan.files_partially_covered == ["backend/services/another_projection.py"]
    assert "chunk_budget_exceeded:primary_backend_logic" in plan.limitations


def test_semantic_chunker_marks_single_file_budget_overflow_partial() -> None:
    oversized = "backend/services/very_long_service_module_name_for_budget_overflow.py"

    plan = build_semantic_chunk_plan(_intake([oversized]), max_chars_per_block=128)

    assert plan.status == "partial"
    assert plan.chunks[0].coverage == "partial"
    assert plan.chunks[0].files == [oversized]
    assert plan.files_partially_covered == [oversized]
    assert "chunk_budget_exceeded:primary_backend_logic" in plan.limitations
