from __future__ import annotations

import socket

import pytest

from app.agent_review.semantic_chunker import (
    IntakeValidationError,
    build_semantic_chunk_plan,
    classify_file,
    extract_files_from_intake,
    validate_intake_contract,
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
    """The prior canonical form (schema_version holding the schema string,
    no schema_id field) must still be accepted during the compatibility
    window (#111) -- flagged with intake_schema_id_missing, exactly as
    before. Never mutate this fixture to the new form; that would defeat
    the point of this specific regression.

    The plan status is deliberately NOT asserted here: the schema envelope is
    an intake-contract fact, not a coverage fact. Which of the two it is
    allowed to drive is the subject of
    test_informational_limitation_alone_never_degrades_the_plan below.
    """

    plan = build_semantic_chunk_plan(_intake(["backend/api/notification_admin.py"]))

    assert "intake_schema_id_missing" in plan.limitations


def test_informational_limitation_alone_never_degrades_the_plan() -> None:
    """AgentEscala#675 / Fix C. A limitation with no coverage consequence
    must not make the plan `partial`.

    The fixture is the legacy-schema intake, so it carries
    `intake_schema_id_missing` -- purely informational, about the intake
    envelope, saying nothing about which files were planned. Every file is
    fully covered: nothing partial, nothing uncovered. Before this fix
    `_plan_status` returned "partial" on `or limitations`, and
    `final_synthesizer._coverage` then republished that as
    `chunk_plan_status_partial` -- a second, apparently independent cause,
    with 100% coverage standing behind it.
    """

    plan = build_semantic_chunk_plan(_intake(["backend/api/notification_admin.py"]))

    # The limitation is still recorded ...
    assert "intake_schema_id_missing" in plan.limitations
    # ... and coverage is provably complete ...
    assert plan.files_covered == ["backend/api/notification_admin.py"]
    assert plan.files_partially_covered == []
    assert plan.files_not_covered == []
    # ... therefore the plan is complete.
    assert plan.status == "complete"


def test_real_coverage_gaps_still_degrade_the_plan() -> None:
    """Fix C must not become fail-open: a limitation that *does* carry a
    coverage consequence keeps degrading the plan exactly as before."""

    partial = build_semantic_chunk_plan(
        _intake(
            [
                "backend/services/notification_event_projection.py",
                "backend/services/another_projection.py",
            ]
        ),
        max_chars_per_block=700,
    )
    assert partial.files_partially_covered != []
    assert partial.status == "partial"

    missing_context = build_semantic_chunk_plan(_intake([]))
    assert missing_context.status == "degraded"
    assert "file_context_missing" in missing_context.limitations

    intake_degraded = build_semantic_chunk_plan(
        _intake(["backend/api/notification_admin.py"], status="degraded")
    )
    assert intake_degraded.status == "degraded"


def test_semantic_chunker_accepts_the_new_schema_id_intake_without_the_limitation() -> None:
    """Issue #111: a real ReviewIntake -- the canonical, currently-constructed
    form, schema_id + integer schema_version -- must no longer carry
    intake_schema_id_missing. This is AgentEscala#675's acceptance
    criterion #1."""

    intake = _intake(["backend/api/notification_admin.py"])
    intake.pop("schema_version")
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = 1

    plan = build_semantic_chunk_plan(intake)

    assert "intake_schema_id_missing" not in plan.limitations


def test_validate_intake_contract_rejects_an_unsupported_integer_schema_version() -> None:
    """Issue #146 thread 7 -- before this fix, the check only verified
    isinstance(schema_version, int), so schema_id + schema_version=2 was
    silently accepted (and callers reusing this function would have inherited
    the same gap)."""

    intake = _intake(["backend/api/notification_admin.py"])
    intake.pop("schema_version")
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = 2

    with pytest.raises(IntakeValidationError) as exc_info:
        validate_intake_contract(intake)

    assert str(exc_info.value) == "intake_schema_version_invalid"


def test_validate_intake_contract_rejects_an_integer_schema_version_without_a_schema_id() -> None:
    """Codex review of PR #156 -- validate_intake_schema_envelope's
    schema-less compatibility branch previously accepted ANY integer
    schema_version (not just the documented descriptive string) once
    schema_id was absent, via `elif isinstance(schema_version, int)`. That
    let an unsupported version such as 2 through with no schema_id present
    -- exactly the gap final_synthesizer.load_intake/quality_gate.load_intake
    were newly exposed to once they started reusing this shared function."""

    intake = _intake(["backend/api/notification_admin.py"])
    intake["schema_version"] = 2

    with pytest.raises(IntakeValidationError) as exc_info:
        validate_intake_contract(intake)

    assert str(exc_info.value) == "intake_schema_invalid"


def test_validate_intake_contract_rejects_a_boolean_or_float_schema_version() -> None:
    """Independent adversarial review of PR #156 -- `schema_version != 1`
    alone would accept `True` (bool is a subclass of int in Python, and
    `True == 1`) or `1.0` (float `==` int is also True) as if they were the
    canonical integer 1."""

    for sneaky_version in (True, 1.0):
        intake = _intake(["backend/api/notification_admin.py"])
        intake.pop("schema_version")
        intake["schema_id"] = "agent-review.intake.v1"
        intake["schema_version"] = sneaky_version

        with pytest.raises(IntakeValidationError) as exc_info:
            validate_intake_contract(intake)

        assert str(exc_info.value) == "intake_schema_version_invalid"


def test_a_real_review_intake_no_longer_carries_the_missing_schema_id_limitation() -> None:
    """End-to-end proof, not just a hand-built dict: a real ReviewIntake
    (schemas.py), built through its own constructor with only the fields
    every production caller actually supplies (cli.py never sets
    schema_version/schema_id explicitly), must already satisfy the new
    canonical form by default."""

    from app.agent_review.schemas import ReviewIntake
    from app.agent_review.semantic_chunker import validate_intake_contract

    intake = ReviewIntake(
        target_repo="mglpsw/aiops-orchestrator",
        target_profile={},
        artifacts={},
        artifact_status=[],
        redaction_summary={"schema_version": "agent-review.redaction-report.v1"},
        status="complete",
    )
    raw = intake.model_dump(mode="json")
    assert raw["schema_id"] == "agent-review.intake.v1"
    assert raw["schema_version"] == 1
    assert "intake_schema_id_missing" not in validate_intake_contract(raw)


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
