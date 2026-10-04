"""Executable contract tests for AgentReview v1 - V1-C2 / Gate A (C2ExecutableContract).

Governing Norm:
- campaign/agent-review-v1-freeze/02_OBLIGATION_MATRIX.json (OBL-CL2-01..07, INV-C2-01..05)
- Issue #221 (AgentReview v1 finalization)

Covers all 5 functional areas:
A1: RawSourceAdmission (SourceState, InvalidSubtype, RelationState, ApplicabilityState)
A2: LegacyCompatibility + Normalization (Legacy flat, mapping packs, named list[str], contract_bindings)
A3: Applicability + Explicit Relations (EffectiveContractRefs, case-sensitive fnmatchcase, unrelated pack isolation)
A4: Canonical Cost + Deterministic Packing (minimal_contracts_context, deterministic FFD)
A5: Required-Context Loss Propagation (shrink ladder priority, fail-closed payload, gate propagation)
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.agent_review import payload_cost_model
from app.agent_review.chunk_payload_builder import build_chunk_payloads
from app.agent_review.pr_brief import build_pr_brief
from app.agent_review.quality_gate import (
    evaluate_review_quality_gate,
    validate_final_review_document,
)
from app.agent_review.schemas import (
    ChunkResults,
    ChunkResultsCoverage,
    FinalReview,
    FinalReviewCounts,
    FinalReviewCoverage,
    FinalReviewRejectedSummary,
    RedactionReport,
    ReviewIntake,
    SemanticChunk,
    SemanticChunkPlan,
)
from app.agent_review.semantic_chunker import build_semantic_chunk_plan


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _base_intake() -> ReviewIntake:
    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "mglpsw/AgentEscala",
            "target_profile": {
                "schema_version": "agent-review.target-profile.v1",
                "target_repo": "mglpsw/AgentEscala",
                "domain_contracts": {
                    "rules": [
                        {"id": "rule-api", "description": "API contract preservation", "paths": ["backend/api/*"]},
                        {"id": "rule-tests", "description": "tests must cover changed behavior", "paths": ["tests/*"]},
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
                            "+token=123",
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
                        "commit_sha": "abc1234567890abcdef1234567890abcdef12345",
                    },
                },
                "validation-evidence-result": {
                    "name": "validation-evidence-result",
                    "path": "validation-evidence/validation-evidence-result.json",
                    "kind": "json",
                    "content": {
                        "validation_verdict": "passed",
                        "blocking_findings": [],
                        "limitations": [],
                    },
                },
            },
            "artifact_status": [
                {"name": "checks", "path": "checks.json", "available": True, "valid": True, "status": "available"},
                {"name": "file-diff-context", "path": "file-diff-context.json", "available": True, "valid": True, "status": "available"},
                {"name": "full-diff", "path": "full.diff", "available": True, "valid": True, "status": "available"},
            ],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-10-03T00:00:00Z",
            "status": "complete",
        }
    )


def _brief(intake: ReviewIntake, chunk_plan: SemanticChunkPlan):
    return build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=RedactionReport(schema_version="agent-review.redaction-report.v1"),
        checks=None,
        validation_evidence=None,
    )


# ---------------------------------------------------------------------------
# A1 — RawSourceAdmission
# ---------------------------------------------------------------------------


def test_cm_a1_malformed_source_emits_typed_invalid_and_not_not_relevant() -> None:
    """CM-A1-MALFORMED-SOURCE: Malformed shape must be INVALID/MALFORMED_SHAPE and never not_relevant."""
    intake = _base_intake()
    intake.target_profile["domain_contracts"] = "string_is_not_a_valid_container"

    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(
        intake.target_profile["domain_contracts"]
    )
    assert contracts == []
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits

    # When evaluated in contracts_context, it must NOT emit contracts_context_not_relevant
    ctx, context_limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in context_limits)
    assert any(lim.startswith("invalid_source_contract:") for lim in context_limits)


def test_pc_a1_malformed_source_positive_control() -> None:
    """PC-A1-MALFORMED-SOURCE: Valid shape yields PRESENT_VALID."""
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(
        {"rules": [{"id": "r1", "description": "rule one"}]}
    )
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype is None
    assert limits == []
    assert len(contracts) == 1


def test_ab_a1_source_absent_ablation() -> None:
    """AB-A1-SOURCE-ABSENT: None source yields ABSENT, not INVALID or MALFORMED."""
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(None)
    assert state == payload_cost_model.SOURCE_STATE_ABSENT
    assert subtype is None
    assert limits == []


def test_cm_a1_unsupported_nonempty_emits_typed_invalid() -> None:
    """CM-A1-UNSUPPORTED-NONEMPTY: Non-empty unsupported shapes must be INVALID/UNSUPPORTED_NONEMPTY."""
    # List containing integers instead of dicts
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts([123, 456])
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_contract:UNSUPPORTED_NONEMPTY" in limits

    # Packs mapping containing non-dict, non-list
    packs, bindings, p_state, p_sub, p_limits = payload_cost_model.normalize_review_packs([123])
    assert p_state == payload_cost_model.SOURCE_STATE_INVALID
    assert p_sub == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY


# ---------------------------------------------------------------------------
# A2 — LegacyCompatibility + Normalization
# ---------------------------------------------------------------------------


def test_cm_a2_legacy_flat_compat() -> None:
    """CM-A2-LEGACY-FLAT-COMPAT: Legacy flat rules and legacy alias 'calendar' must be preserved (GREEN_PRESERVATION)."""
    intake = _base_intake()
    # Legacy alias calendar
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="calendar",
        semantic_group="api_schema_contract",
    )
    pack_ids = [p["id"] for p in ctx["review_packs"]]
    assert "agentescala-calendar" in pack_ids
    assert not any(lim.startswith("selected_contract_pack_missing:") for lim in limits)


def test_cm_a2_named_string_list() -> None:
    """CM-A2-NAMED-STRING-LIST: Section with named list[str] must normalize without fabricated claim_ids."""
    raw = {
        "auth_security_rules": [
            "Ensure tokens are redacted",
            "Require role validation",
        ]
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert len(contracts) == 1
    assert contracts[0]["id"] == "auth_security_rules"
    assert contracts[0]["rules"] == [
        "Ensure tokens are redacted",
        "Require role validation",
    ]


def test_cm_a2_malformed_contract_bindings() -> None:
    """CM-A2-MALFORMED-CONTRACT_BINDINGS: Malformed contract_bindings field must emit typed limitation."""
    # String instead of mapping
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(
        {"packs": [{"id": "p1"}]},
        extra_bindings="not_a_mapping",
    )
    assert any(lim.startswith("malformed_contract_bindings:") for lim in limits)

    # Value is int instead of list of contract IDs
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(
        {"packs": [{"id": "p1"}]},
        extra_bindings={"p1": 999},
    )
    assert any(lim.startswith("malformed_contract_bindings:") for lim in limits)


def test_pc_a2_contract_bindings_positive_control() -> None:
    """PC-A2-CONTRACT-BINDINGS: Valid mapping is cleanly resolved."""
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(
        {"packs": [{"id": "p1"}]},
        extra_bindings={"p1": ["contract-a", "contract-b"]},
    )
    assert limits == []
    assert bindings == {"p1": ["contract-a", "contract-b"]}


# ---------------------------------------------------------------------------
# A3 — Applicability + Explicit Relations
# ---------------------------------------------------------------------------


def test_cm_a3_binding_auth_admin_and_response_model_rules() -> None:
    """CM-A3-BINDING-AUTH-ADMIN & CM-A3-BINDING-RESPONSE-MODEL-RULES:
    EffectiveContractRefs(pack_id) = {domain_contract} UNION contract_bindings[pack_id]
    deduplicated exactly and marked required=True on matching chunks.
    """
    intake = _base_intake()
    intake.target_profile = {
        "schema_version": "agent-review.target-profile.v1",
        "target_repo": "mglpsw/AgentEscala",
        "domain_contracts": [
            {"id": "admin_core", "description": "Admin core contract"},
            {"id": "auth_contract", "description": "Auth security contract"},
            {"id": "response_model_rules", "description": "Response model schemas"},
        ],
        "review_packs": {
            "packs": {
                "admin_pack": {
                    "paths": ["backend/api/*"],
                    "domain_contract": "admin_core",
                    "recommended_review_preset": "review:deep",
                }
            },
            "contract_bindings": {
                "admin_pack": ["auth_contract", "response_model_rules", "admin_core"]
            },
        },
    }

    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    assert limits == []
    pack = next(p for p in ctx["review_packs"] if p["id"] == "admin_pack")
    assert pack["effective_contracts"] == ["admin_core", "auth_contract", "response_model_rules"]

    # All three must be present in domain_contracts and flagged required=True
    contract_ids = {c["id"]: c.get("required") for c in ctx["domain_contracts"]}
    assert contract_ids.get("admin_core") is True
    assert contract_ids.get("auth_contract") is True
    assert contract_ids.get("response_model_rules") is True


def test_cm_a3_unrelated_pack_does_not_match_and_does_not_leak_contracts() -> None:
    """CM-A3-UNRELATED-PACK: A pack with non-matching paths must not attach, and its bound contracts must not leak."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "admin_contract", "paths": ["backend/admin/*"]},
            {"id": "billing_contract", "paths": ["backend/billing/*"]},
        ],
        "review_packs": {
            "packs": {
                "admin_pack": {"paths": ["backend/admin/*"], "domain_contract": "admin_contract"},
                "billing_pack": {"paths": ["backend/billing/*"], "domain_contract": "billing_contract"},
            }
        },
    }

    # Chunk is in admin only
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/admin/users.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="primary_backend_logic",
    )
    pack_ids = [p["id"] for p in ctx["review_packs"]]
    assert "admin_pack" in pack_ids
    assert "billing_pack" not in pack_ids

    contract_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert "admin_contract" in contract_ids
    assert "billing_contract" not in contract_ids


def test_cm_a3_unknown_binding_emits_unresolved_limitation() -> None:
    """CM-A3-UNKNOWN-BINDING: Binding pointing to non-existent contract ID must emit unresolved_contract_binding."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [{"id": "rule-exists"}],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/*"]}},
            "contract_bindings": {"pack1": ["rule-does-not-exist"]},
        },
    }

    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="primary_backend_logic",
    )
    assert "unresolved_contract_binding:pack1:rule-does-not-exist" in limits


def test_cm_a3_case_sensitive_pattern_matching() -> None:
    """CM-A3-CASE-SENSITIVE-PATTERN: Pattern matching must use case-sensitive fnmatchcase."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "rule-case-wrong", "patterns": ["Backend/Api/*.py"]},
            {"id": "rule-case-right", "patterns": ["backend/api/*.py"]},
        ]
    }

    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="primary_backend_logic",
    )
    contract_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert "rule-case-right" in contract_ids
    assert "rule-case-wrong" not in contract_ids


# ---------------------------------------------------------------------------
# A4 — Canonical Cost + Deterministic Packing
# ---------------------------------------------------------------------------


def test_cm_ga_a4_cost_and_minimal_contracts_context() -> None:
    """CM-GA-A4-COST: Minimal contracts context preserves required=True contracts,
    and projected cost provides a sound upper bound for payload packaging.
    """
    full_ctx = {
        "domain_contracts": [
            {"id": "c-optional", "description": "optional contract"},
            {"id": "c-required", "description": "required contract", "required": True},
        ],
        "review_packs": [{"id": "p1"}],
    }
    min_ctx = payload_cost_model.minimal_contracts_context(full_ctx)
    min_ids = [c["id"] for c in min_ctx["domain_contracts"]]
    assert "c-required" in min_ids
    assert "c-optional" not in min_ids
    assert min_ctx["review_packs"] == []


def test_cm_ga_a4_repack_determinism() -> None:
    """CM-GA-A4-REPACK: Semantic chunk planning and packaging is fully deterministic across repetitions."""
    intake = _base_intake()

    intake_dict = intake.model_dump(mode="json")
    plan1 = build_semantic_chunk_plan(intake_dict, max_blocks=3, max_chars_per_block=8000)
    plan2 = build_semantic_chunk_plan(intake_dict, max_blocks=3, max_chars_per_block=8000)

    assert plan1.model_dump_json() == plan2.model_dump_json()
    assert [c.chunk_id for c in plan1.chunks] == [c.chunk_id for c in plan2.chunks]


# ---------------------------------------------------------------------------
# A5 — Required-Context Loss Propagation
# ---------------------------------------------------------------------------


def test_cm_a5_required_context_dropped_shrink_ladder_order() -> None:
    """CM-A5-REQUIRED-CONTEXT-DROPPED: Optional contracts must be shrunk before required contracts."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-1", "description": "X" * 100, "paths": ["backend/api/*"]},
            {"id": "opt-1", "description": "Y" * 100, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-1"]},
        },
    }

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    brief = _brief(intake, plan)

    # Test shrinking payload with budget that fits only the required contract
    # We find a budget where opt-1 is dropped but req-1 is kept
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    # With full budget, both contracts are present
    payload = next(iter(payloads.values()))
    c_ids = [c["id"] for c in payload.chunk_context["contracts_context"]["domain_contracts"]]
    assert "req-1" in c_ids
    assert "opt-1" in c_ids


def test_cm_a5_budget_loss_marks_limited_and_payload_path_none() -> None:
    """CM-A5-BUDGET-LOSS: When budget forces loss of a required contract,
    it records required_contract_context_lost:<id>, status='limited', and payload_path=None.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-huge", "description": "Z" * 5000, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-huge"]},
        },
    }

    # Construct chunk plan, then constrain prompt_budget_chars to be too small for the 5000-char contract
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    for c in plan.chunks:
        c.prompt_budget_chars = 2500  # fits hunks and brief, but too small to fit 5000-char req-huge

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    # Manifest entries that lost required contract context must have payload_path=None and status="limited"
    limited_entries = [e for e in manifest.chunks if any(lim.startswith("required_contract_context_lost:req-huge") for lim in e.limitations)]
    assert len(limited_entries) > 0
    for entry in limited_entries:
        assert entry.status == "limited"
        assert entry.payload_path is None


def test_cm_a5_limitation_dropped_before_gate_propagates_to_quality_gate() -> None:
    """CM-A5-LIMITATION-DROPPED-BEFORE-GATE:
    Critical contract limitations in chunk results or final review force input_degraded=True,
    preventing quality gate status from ever being 'passed'.
    """
    intake = _base_intake()
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"))

    chunk_results = ChunkResults(
        target_repo="mglpsw/AgentEscala",
        chunk_plan_ref={"status": "degraded"},
        chunks_parsed=["chunk-01"],
        chunks_failed=[],
        confirmed_findings=[],
        risks=[],
        limitations=["required_contract_context_lost:req-1"],
        model_reported_limitations=[],
        rejected_findings=[],
        coverage=ChunkResultsCoverage(
            files_reviewed=["backend/api/shifts.py", "tests/test_shift_service.py"],
            files_partial=[],
            files_not_reviewed=[],
        ),
        status="degraded",
    )

    final_review = FinalReview(
        schema_id="agent-review.final-review.v1",
        schema_version=1,
        target_repo="mglpsw/AgentEscala",
        created_at="2026-10-03T00:00:00Z",
        status="degraded",
        verdict="approved",  # Model claims approved, but input was degraded by contract loss
        summary="Looks good despite contract loss",
        counts=FinalReviewCounts(
            confirmed_findings_total=0,
            findings_by_severity={},
            risks_total=0,
            risks_by_source={},
            rejected_findings_total=0,
            rejected_findings_by_reason={},
        ),
        findings=[],
        risks=[],
        coverage=FinalReviewCoverage(
            files_reviewed=["backend/api/shifts.py", "tests/test_shift_service.py"],
            files_partial=[],
            files_not_reviewed=[],
        ),
        limitations=["required_contract_context_lost:req-1"],
    )

    final_review_doc = validate_final_review_document(final_review.model_dump(mode="json"))

    gate = evaluate_review_quality_gate(
        final_review=final_review_doc,
        chunk_results=chunk_results,
        intake=intake,
        chunk_plan=plan,
    )

    # Gate MUST NOT pass approved! It must require manual review.
    assert gate.status == "manual_review_required"
    assert gate.manual_review_required is True
    assert gate.normalized_verdict == "manual_review_required"
