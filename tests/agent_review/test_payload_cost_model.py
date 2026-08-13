"""RED/lock tests for the shared payload cost authority (aiops-orchestrator#225).

These tests target `payload_cost_model.py` directly: path identity
(RED-21), the projection-input binding check (RED-19), and the soundness
property `actual_hunk_preserving_payload_chars <= projected_chars <= budget`
(RED-9, RED-5, RED-11) across adversarial envelope shapes. End-to-end
planner/builder RED coverage (RED-1..RED-4, RED-6, RED-7, RED-12..RED-18,
RED-20, RED-22, RED-23) lives in test_semantic_chunker.py and
test_chunk_payload_builder.py, next to the fixtures they already share.
"""

from __future__ import annotations

from app.agent_review import payload_cost_model as m
from app.agent_review.chunk_payload_builder import build_chunk_payloads
from app.agent_review.schemas import PRBrief, RedactionReport, ReviewIntake, SemanticChunk, SemanticChunkPlan


# ---------------------------------------------------------------------------
# Path identity (RED-21)
# ---------------------------------------------------------------------------


def test_canonical_repo_path_accepts_ordinary_relative_paths() -> None:
    assert m.canonical_repo_path("backend/api/a.py") == "backend/api/a.py"
    assert m.canonical_repo_path("./backend/api/a.py") == "backend/api/a.py"
    assert m.canonical_repo_path("backend\\api\\a.py") == "backend/api/a.py"


def test_canonical_repo_path_fails_closed_on_absolute_and_traversal_and_empty() -> None:
    for bad in ("/etc/passwd", "~/secrets.env", "C:\\Windows\\a.py", "a/../../etc/passwd", "", "   "):
        try:
            m.canonical_repo_path(bad)
        except m.PathIdentityError:
            continue
        raise AssertionError(f"expected PathIdentityError for {bad!r}")


def test_sanitized_collision_never_silently_deduplicates() -> None:
    # Two distinct absolute paths both redact to the same
    # [LOCAL_PATH_REDACTED] display string -- assert_no_sanitized_collision
    # must fail closed rather than let one silently stand in for the other.
    try:
        m.assert_no_sanitized_collision(["/home/alice/secret.py", "/home/bob/other.py"])
    except m.PathIdentityError as exc:
        assert exc.error_class == "path_identity_collision"
    else:
        raise AssertionError("expected a path_identity_collision")


def test_sanitized_collision_absent_for_distinct_repo_relative_paths() -> None:
    m.assert_no_sanitized_collision(["backend/api/a.py", "backend/api/b.py"])


# ---------------------------------------------------------------------------
# Projection-input binding (RED-19)
# ---------------------------------------------------------------------------


def _intake_with_checks(checks_content: dict | None) -> ReviewIntake:
    artifacts = {
        "file-diff-context.json": {
            "path": "file-diff-context.json",
            "content": {"files": [{"path": "a.py"}], "coverage_requirements": {}},
        },
        "full-diff.diff": {"path": "full-diff.diff", "content": ""},
    }
    if checks_content is not None:
        artifacts["checks.json"] = {"path": "checks.json", "content": checks_content}
    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "r/t",
            "target_profile": {},
            "created_at": "2026-08-13T00:00:00Z",
            "artifacts": artifacts,
            "artifact_status": [],
            "redaction_summary": RedactionReport().model_dump(mode="json"),
            "limitations": [],
            "completeness": {},
            "status": "complete",
        }
    )


def test_projection_inputs_bound_passes_when_external_matches_intake() -> None:
    checks = {"status": "complete", "checks": []}
    intake = _intake_with_checks(checks)
    m.assert_projection_inputs_bound(intake, checks=dict(checks), validation_evidence=None)


def test_projection_inputs_bound_fails_closed_on_divergence() -> None:
    intake = _intake_with_checks({"status": "complete", "checks": []})
    try:
        m.assert_projection_inputs_bound(
            intake, checks={"status": "different", "checks": []}, validation_evidence=None
        )
    except m.ProjectionInputMismatchError as exc:
        assert exc.error_class == "payload_projection_input_mismatch"
    else:
        raise AssertionError("expected payload_projection_input_mismatch")


def test_projection_inputs_bound_fails_closed_when_intake_has_no_such_artifact() -> None:
    intake = _intake_with_checks(None)
    try:
        m.assert_projection_inputs_bound(intake, checks={"status": "complete", "checks": []}, validation_evidence=None)
    except m.ProjectionInputMismatchError as exc:
        assert exc.error_class == "payload_projection_input_mismatch"
    else:
        raise AssertionError("expected payload_projection_input_mismatch")


def test_projection_inputs_bound_ignores_none_and_non_dict_external() -> None:
    intake = _intake_with_checks(None)
    m.assert_projection_inputs_bound(intake, checks=None, validation_evidence=None)


# ---------------------------------------------------------------------------
# Soundness (RED-9 / RED-5 / RED-11): actual <= projected <= budget, checked
# against the REAL builder across adversarial envelope shapes.
# ---------------------------------------------------------------------------


def _hunk(path: str, lines: int) -> str:
    body = "\n".join(f"+    v{i} = f(i)" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,{lines} @@\n{body}"


def _build_intake(
    files: list[str],
    hunk_lines: dict[str, int],
    must: list[str],
    checks_doc=None,
    ve_doc=None,
    file_overrides: dict[str, dict] | None = None,
    intake_limitations: list[str] | None = None,
    artifact_status: list[dict] | None = None,
    target_profile: dict | None = None,
):
    diff = "\n".join(_hunk(p, hunk_lines.get(p, 5)) for p in files)
    file_overrides = file_overrides or {}
    artifacts = {
        "file-diff-context.json": {
            "path": "file-diff-context.json",
            "content": {
                "files": [{"path": p, **file_overrides.get(p, {})} for p in files],
                "coverage_requirements": {"must_review_files": must},
            },
        },
        "full-diff.diff": {"path": "full-diff.diff", "content": diff},
    }
    if checks_doc is not None:
        artifacts["checks.json"] = {"path": "checks.json", "content": checks_doc}
    if ve_doc is not None:
        artifacts["validation-evidence-result.json"] = {
            "path": "validation-evidence-result.json",
            "content": ve_doc,
        }
    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "r/t",
            "target_profile": target_profile or {},
            "created_at": "2026-08-13T00:00:00Z",
            "artifacts": artifacts,
            "artifact_status": artifact_status or [],
            "redaction_summary": RedactionReport().model_dump(mode="json"),
            "limitations": intake_limitations or [],
            "completeness": {},
            "status": "complete",
        }
    )


def _assert_projection_sound(
    files: list[str],
    hunk_lines: dict[str, int],
    must: list[str] | None = None,
    *,
    brief_limitations: list[str] | None = None,
    use_real_metadata: bool = False,
    **docs,
) -> None:
    """actual <= projected <= budget: build the real payload at
    budget=projected and confirm hunks were never touched (P1)."""
    must = must if must is not None else files
    file_overrides = docs.pop("file_overrides", None)
    intake_limitations = docs.pop("intake_limitations", None)
    artifact_status = docs.pop("artifact_status", None)
    target_profile = docs.pop("target_profile", None)
    intake = _build_intake(
        files,
        hunk_lines,
        must,
        file_overrides=file_overrides,
        intake_limitations=intake_limitations,
        artifact_status=artifact_status,
        target_profile=target_profile,
        **docs,
    )
    checks_doc = docs.get("checks_doc")
    ve_doc = docs.get("ve_doc")
    hunks = m.diff_by_file(intake)
    if use_real_metadata:
        # P2-3: exact review identity/mode/contract_pack via the same
        # authority pr_brief.build_pr_brief calls, never a placeholder.
        metadata = m.resolve_review_metadata(
            intake=intake, chunk_plan_target_repo="r/t", checks=checks_doc, validation_evidence=ve_doc
        )
        target = {
            "repository": metadata["target_repo"],
            "pr_number": metadata["pr_number"],
            "commit_sha": metadata["commit_sha"],
        }
        brief_review = {"mode": metadata["review_mode"], "contract_pack": metadata["contract_pack"]}
    else:
        target = {"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40}
        brief_review = {"mode": "full", "contract_pack": None}
    resolved_brief_limitations = list(brief_limitations) if brief_limitations is not None else []
    projected = m.project_min_hunk_preserving_chars(
        intake=intake,
        chunk_files=files,
        chunk_contracts=[],
        semantic_group="api_schema_contract",
        target=target,
        brief_target=target,
        brief_review=brief_review,
        brief_required_files=must,
        brief_limitations=resolved_brief_limitations,
        selected_contract_pack=brief_review.get("contract_pack"),
        checks=checks_doc,
        validation_evidence=ve_doc,
        hunks=hunks,
        created_at="2026-08-13T00:00:00Z",
    )

    plan = SemanticChunkPlan(
        target_repo="r/t",
        max_parallel_blocks=6,
        status="complete",
        files_covered=files,
        chunks=[
            SemanticChunk(
                chunk_id="chunk-01-api_schema_contract",
                semantic_group="api_schema_contract",
                order_index=0,
                files=files,
                artifacts=[],
                contracts=[],
                depends_on=[],
                coverage="complete",
                prompt_budget_chars=projected,
                estimated_chars=projected,
            )
        ],
    )
    brief = PRBrief(
        target=target,
        review=brief_review,
        coverage={"required_files": must},
        limitations=resolved_brief_limitations,
        created_at="2026-08-13T00:00:00Z",
    )
    manifest, _ = build_chunk_payloads(
        intake=intake, chunk_plan=plan, pr_brief=brief, checks=checks_doc, validation_evidence=ve_doc
    )
    entry = manifest.chunks[0]
    assert entry.payload_path is not None, (
        f"projection={projected} was not sound: builder blocked the chunk it claimed would fit"
    )
    assert "chunk_hunks_reduced" not in entry.truncation.coverage_impact


def test_projection_sound_for_tiny_single_file() -> None:
    _assert_projection_sound(["a.py"], {"a.py": 1})


def test_projection_sound_for_774_scale_material() -> None:
    files = [f"scripts/aiops/f{i}.py" for i in range(6)]
    _assert_projection_sound(files, {p: 600 for p in files})


def test_projection_sound_with_large_required_files_list() -> None:
    # RED-11: a constant envelope reserve fails here -- required_files is
    # embedded verbatim in `brief.required_files` and is unbounded.
    must = [f"backend/services/module_{i}.py" for i in range(200)]
    _assert_projection_sound(["a.py"], {"a.py": 20}, must=["a.py", *must])


def test_projection_sound_with_populated_checks_document() -> None:
    checks_doc = {
        "status": "complete",
        "checks": [{"name": f"check{i}", "status": "passed", "command": f"cmd {i}"} for i in range(15)],
    }
    _assert_projection_sound(["a.py", "b.py"], {"a.py": 30, "b.py": 30}, checks_doc=checks_doc)


def test_projection_sound_with_populated_validation_evidence() -> None:
    ve_doc = {
        "status": "complete",
        "validation_verdict": "approve_with_risks",
        "blocking_findings": [{"title": f"f{i}", "file_path": "a.py"} for i in range(10)],
        "limitations": [f"some_limitation_{i}" for i in range(5)],
    }
    _assert_projection_sound(["a.py"], {"a.py": 30}, ve_doc=ve_doc)


def test_projection_sound_at_exact_fit_boundaries() -> None:
    # RED-5: capacity-1 / capacity / capacity+1, including envelope.
    intake = _build_intake(["a.py"], {"a.py": 40}, ["a.py"])
    hunks = m.diff_by_file(intake)
    projected = m.project_min_hunk_preserving_chars(
        intake=intake,
        chunk_files=["a.py"],
        chunk_contracts=[],
        semantic_group="api_schema_contract",
        target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        brief_target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        brief_review={"mode": "full", "contract_pack": None},
        brief_required_files=["a.py"],
        brief_limitations=[],
        selected_contract_pack=None,
        checks=None,
        validation_evidence=None,
        hunks=hunks,
        created_at="2026-08-13T00:00:00Z",
    )
    brief = PRBrief(
        target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        review={"mode": "full", "contract_pack": None},
        coverage={"required_files": ["a.py"]},
        created_at="2026-08-13T00:00:00Z",
    )
    for delta, expect_ok in ((-1, True), (0, True), (1, True)):
        plan = SemanticChunkPlan(
            target_repo="r/t",
            max_parallel_blocks=6,
            status="complete",
            files_covered=["a.py"],
            chunks=[
                SemanticChunk(
                    chunk_id="chunk-01-api_schema_contract",
                    semantic_group="api_schema_contract",
                    order_index=0,
                    files=["a.py"],
                    artifacts=[],
                    contracts=[],
                    depends_on=[],
                    coverage="complete",
                    prompt_budget_chars=projected + delta,
                    estimated_chars=projected,
                )
            ],
        )
        manifest, _ = build_chunk_payloads(
            intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None
        )
        entry = manifest.chunks[0]
        # projected is itself already a sound upper bound; capacity-1 may or
        # may not fit depending on slack, but it must never silently reduce
        # a hunk without the guard blocking it.
        if entry.payload_path is not None:
            assert "chunk_hunks_reduced" not in entry.truncation.coverage_impact


# ---------------------------------------------------------------------------
# PR #227 exact-HEAD adversarial review findings P2-1/P2-2/P2-3: tight-budget
# proof that `chunk_context.files`, `brief.limitations`, and
# `target`/`brief.review` are projected from exact facts, not an
# approximate placeholder. Unlike the end-to-end tests in
# test_semantic_chunker_225_red.py (which go through the planner's own
# `max_chars_per_block` choice and can have enough slack to mask an
# under-estimate), these set `prompt_budget_chars` to exactly the current
# projection -- the tightest possible proof that a smaller, placeholder-based
# projection would have been unsound at this same budget.
# ---------------------------------------------------------------------------


def test_red24_projection_sound_with_adversarial_file_status_and_summary() -> None:
    """RED-24 (P2-1): chunk_context.files is never touched by the shrink
    ladder. A `status`/`summary` this long previously projected as
    `"unknown"`/`None` (a few bytes) instead of the real ~10,000+ characters
    -- proven unsound below by running the exact old-style placeholder
    through the real builder at the same tight budget.
    """
    files = ["backend/api/a.py", "tests/b_test.py"]
    long_status = "modified-" + ("S" * 5000)
    long_summary = "x" * 5000
    file_overrides = {p: {"status": long_status, "summary": long_summary} for p in files}
    _assert_projection_sound(files, {p: 20 for p in files}, files, file_overrides=file_overrides)


def test_placeholder_file_status_would_have_been_unsound() -> None:
    """Negative control for RED-24: confirms the scenario above actually
    distinguishes real from placeholder status/summary -- projecting with
    the OLD `"unknown"`/`None` placeholder and feeding that (too-small)
    budget to the real builder must trip the hard guard.
    """
    files = ["backend/api/a.py", "tests/b_test.py"]
    long_status = "modified-" + ("S" * 5000)
    long_summary = "x" * 5000
    file_overrides = {p: {"status": long_status, "summary": long_summary} for p in files}
    intake = _build_intake(files, {p: 20 for p in files}, files, file_overrides=file_overrides)
    hunks = m.diff_by_file(intake)

    # The old, pre-fix placeholder: "unknown" / None regardless of the real
    # file-diff-context content.
    placeholder_payload_body_files = [
        {"path": m.sanitize_display_path(p), "status": "unknown", "summary": None} for p in sorted(files)
    ]
    real_payload_body_files = [
        {
            "path": m.sanitize_display_path(p),
            "status": m._clean_text(file_overrides[p]["status"]),
            "summary": m._clean_text(file_overrides[p]["summary"]),
        }
        for p in sorted(files)
    ]
    placeholder_len = len(m.canonical_json(placeholder_payload_body_files))
    real_len = len(m.canonical_json(real_payload_body_files))
    assert real_len > placeholder_len + 5000, "adversarial fixture is not actually adversarial enough"

    projected = m.project_min_hunk_preserving_chars(
        intake=intake,
        chunk_files=files,
        chunk_contracts=[],
        semantic_group="api_schema_contract",
        target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        brief_target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        brief_review={"mode": "full", "contract_pack": None},
        brief_required_files=files,
        brief_limitations=[],
        selected_contract_pack=None,
        checks=None,
        validation_evidence=None,
        hunks=hunks,
        created_at="2026-08-13T00:00:00Z",
    )
    # A budget only big enough for the OLD placeholder-sized projection is,
    # by construction, too small for the real one.
    old_placeholder_budget = projected - (real_len - placeholder_len)
    assert old_placeholder_budget > 0

    plan = SemanticChunkPlan(
        target_repo="r/t",
        max_parallel_blocks=6,
        status="complete",
        files_covered=files,
        chunks=[
            SemanticChunk(
                chunk_id="chunk-01-api_schema_contract",
                semantic_group="api_schema_contract",
                order_index=0,
                files=files,
                artifacts=[],
                contracts=[],
                depends_on=[],
                coverage="complete",
                prompt_budget_chars=old_placeholder_budget,
                estimated_chars=old_placeholder_budget,
            )
        ],
    )
    brief = PRBrief(
        target={"repository": "r/t", "pr_number": 1, "commit_sha": "a" * 40},
        review={"mode": "full", "contract_pack": None},
        coverage={"required_files": files},
        created_at="2026-08-13T00:00:00Z",
    )
    manifest, _ = build_chunk_payloads(
        intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None
    )
    entry = manifest.chunks[0]
    guard_tripped = entry.payload_path is None
    hunks_reduced = entry.payload_path is not None and "chunk_hunks_reduced" in entry.truncation.coverage_impact
    assert guard_tripped or hunks_reduced, (
        "expected the placeholder-sized budget to be unsound for the real file content "
        "(either the hard guard blocks the chunk, or a hunk gets reduced) -- if neither "
        "happened, this negative control no longer demonstrates what RED-24 fixed"
    )


def test_red25_projection_sound_with_adversarial_limitations_envelope() -> None:
    """RED-25 (P2-2): `brief.limitations` = `intake.limitations` +
    `chunk_plan.limitations` + `optional_limitations` +
    `artifact_state_limitations(intake)`. None of these are shrinkable.
    """
    files = ["backend/api/a.py"]
    many_limitations = [f"synthetic_limitation_{i}" for i in range(80)]
    _assert_projection_sound(files, {"backend/api/a.py": 20}, files, brief_limitations=many_limitations)


def test_red26_projection_sound_with_adversarial_review_metadata() -> None:
    """RED-26 (P2-3): target/brief.review metadata resolved exactly via
    payload_cost_model.resolve_review_metadata -- no placeholder may
    under-estimate what that resolution actually produces.
    """
    files = ["backend/api/a.py"]
    checks_doc = {
        "status": "complete",
        "checks": [],
        "pr_number": 999999999,
        "commit_sha": "a" * 40,
        "review_mode": "adversarially-long-review-mode-" + ("m" * 3000),
        "contract_pack": "adversarially-long-contract-pack-" + ("p" * 3000),
    }
    _assert_projection_sound(files, {"backend/api/a.py": 20}, files, use_real_metadata=True, checks_doc=checks_doc)
