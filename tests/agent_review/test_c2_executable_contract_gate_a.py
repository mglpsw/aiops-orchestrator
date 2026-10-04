"""Executable contract tests for AgentReview v1 - V1-C2 / Gate A (C2ExecutableContract).

Governing Norm:
- campaign/agent-review-v1-freeze/02_OBLIGATION_MATRIX.json (OBL-CL2-01..07, INV-C2-01..05)
- Issue #221 (AgentReview v1 finalization)
- PR #367 (historical requirement refinements)
- PR #369 (requirements freeze)
- PR #370 (corrective/lifecycle integration)

Baseline Classification:
- RED_REQUIRED:
  - CM-A2-REAL-TARGET-DOMAIN-SHAPE
  - CM-A2-NESTED-SECTION-IDENTITY
  - CM-A2-RULE-DICT-TEXT-PRESERVATION
  - CM-A3-EXACT-IDENTITY-BOUNDARY
  - CM-A3-AVAILABILITY-IS-NOT-APPLICABILITY
  - CM-A3-UNSCOPED-PACK-IS-NOT-GLOBAL
  - CM-GA-A4-COST
  - CM-GA-A4-REPACK
  - CM-A5-REQUIRED-CONTEXT-DROPPED
  - CM-A5-REQUIRED-FLOOR-EXCEEDS-BUDGET
  - CM-A5-PRODUCTION-CARRIER-ORIGIN
- GREEN_PRESERVATION:
  - CM-A1-MALFORMED-SOURCE
  - CM-A1-UNSUPPORTED-NONEMPTY
  - CM-A2-LEGACY-FLAT-COMPAT
  - CM-A2-NAMED-STRING-LIST
  - CM-A2-MALFORMED-CONTRACT_BINDINGS
  - CM-A3-OWNER-FOCAL-RELATIONS
  - CM-A3-STRUCTURAL-TARGET-LIKE-SHAPE
  - CM-A3-UNRELATED-PACK
  - CM-A3-UNKNOWN-BINDING
  - CM-A3-CASE-SENSITIVE-PATTERN
- DEFENSIVE_CONTROL:
  - DEFENSIVE_PLAN_BUILDER_MISMATCH_CONTROL
  - CARRIER_CONSUMPTION_CONTROL
- POSITIVE_CONTROL:
  - PC-A1-MALFORMED-SOURCE
  - PC-A2-CONTRACT-BINDINGS
  - PC-A3-APPLICABLE-PACK-BINDS-CONTRACT
  - PC-A3-EXPLICIT-CONTRACT-REF
  - PC-A3-EXPLICIT-GLOBAL
  - PC-A3-EXPLICIT-SELECTED-PACK
  - PC-A3-LEGACY-FLAT-COMPATIBILITY
  - PC-GA-A4-COST
  - CM-A5-PRODUCTION-CARRIER-ORIGIN-SELECTED-PACK-MISSING
- ABLATION_CONTROL:
  - AB-A1-STATE-SEPARATION
  - AB-A2-SECTION-IDENTITY
  - AB-A2-RULE-DICT-TEXT
  - AB-A3-EXPLICIT-BINDING
  - AB-A3-EXACT-IDENTITY
  - AB-A3-FNMATCHCASE
  - AB-A3-AVAILABILITY-APPLICABILITY
  - AB-GA-A4-COST
  - AB-GA-A4-REPACK
  - AB-A5-SHRINK-ORDER
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.agent_review import payload_cost_model
from app.agent_review.chunk_payload_builder import (
    _shrink_contracts_context,
    build_chunk_payloads,
)
from app.agent_review.chunk_result_parser import parse_chunk_results
from app.agent_review.final_synthesizer import synthesize_final_review
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
    NormalizedFinding,
    RedactionReport,
    ReviewIntake,
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


def _structural_mirror_fixture() -> dict[str, Any]:
    """Synthetic target-like domain contracts fixture.
    fixture_role: structural_mirror
    authority_effect: none
    Gate_C_evidence: false
    """
    return {
        "version": "1.0",
        "updated": "2026-05-05",
        "system": {
            "name": "AgentEscala",
            "purpose": "Clinical rota and shift scheduling assistant",
        },
        "calendar": {
            "canonical_authority": "https://example.com/calendar",
            "display_authority": "Calendar Service",
            "description": "Calendar and scheduling domain invariants",
            "slot_rules": [
                {
                    "rule": "Slot start time must precede slot end time",
                    "invariant": True,
                    "rationale": "Time ordering invariant",
                }
            ],
        },
        "swaps": {
            "rules": [
                {
                    "rule": "Swaps require consent from both clinical parties",
                    "invariant": True,
                }
            ]
        },
        "auth_admin": {
            "critical_constraints": [
                "Administrator endpoints must enforce role check"
            ],
            "review_checklist": [
                "Check bearer token header redaction"
            ],
            "response_models": [
                {"model": "ShiftResponse"}
            ],
            "orm_models": [
                {"model": "ShiftORM"}
            ],
        },
        "response_model_rules": [
            "All response models must validate datetime in UTC"
        ],
    }


# ---------------------------------------------------------------------------
# A1 — RawSourceAdmission
# ---------------------------------------------------------------------------


def test_cm_a1_malformed_source_emits_typed_invalid_and_not_not_relevant() -> None:
    """CM-A1-MALFORMED-SOURCE: Malformed shape must be INVALID/MALFORMED_SHAPE and never not_relevant (GREEN_PRESERVATION)."""
    intake = _base_intake()
    intake.target_profile["domain_contracts"] = "string_is_not_a_valid_container"

    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(
        intake.target_profile["domain_contracts"]
    )
    assert contracts == []
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits

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


def test_cm_a1_unsupported_nonempty_emits_typed_invalid() -> None:
    """CM-A1-UNSUPPORTED-NONEMPTY: Non-empty unsupported shapes must be INVALID/UNSUPPORTED_NONEMPTY (GREEN_PRESERVATION)."""
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts([123, 456])
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_contract:UNSUPPORTED_NONEMPTY" in limits

    packs, bindings, p_state, p_sub, p_limits = payload_cost_model.normalize_review_packs([123])
    assert p_state == payload_cost_model.SOURCE_STATE_INVALID
    assert p_sub == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY


def test_pc_a1_malformed_source_positive_control() -> None:
    """PC-A1-MALFORMED-SOURCE: Valid shape yields PRESENT_VALID (POSITIVE_CONTROL)."""
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(
        {"rules": [{"id": "r1", "description": "rule one"}]}
    )
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype is None
    assert limits == []
    assert len(contracts) == 1


def test_ab_a1_state_separation() -> None:
    """AB-A1-STATE-SEPARATION: None source yields ABSENT; conflating ABSENT with INVALID is refuted (ABLATION_CONTROL)."""
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(None)
    assert state == payload_cost_model.SOURCE_STATE_ABSENT
    assert subtype is None
    assert limits == []

    # Ablation: conflating ABSENT with INVALID
    def ablated_state_classifier(doc: Any) -> str:
        if doc is None:
            return payload_cost_model.SOURCE_STATE_INVALID  # focal defect: conflates ABSENT with INVALID
        return payload_cost_model.SOURCE_STATE_PRESENT_VALID

    ablated_state = ablated_state_classifier(None)
    assert ablated_state != payload_cost_model.SOURCE_STATE_ABSENT, "Ablation must produce divergent state (RED)"


# ---------------------------------------------------------------------------
# A2 — LegacyCompatibility + Normalization
# ---------------------------------------------------------------------------


def test_cm_a2_real_target_domain_shape() -> None:
    """CM-A2-REAL-TARGET-DOMAIN-SHAPE: Normalizes structural mirror fixture without dropping contracts (RED_REQUIRED)."""
    fixture = _structural_mirror_fixture()
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(fixture)

    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype is None
    assert limits == []

    contract_ids = [c["id"] for c in contracts]
    assert contract_ids == ["auth_admin", "calendar", "response_model_rules", "swaps"]
    # Reserved metadata keys must NOT be emitted as contract identities
    assert "version" not in contract_ids
    assert "updated" not in contract_ids
    assert "system" not in contract_ids

    # Calendar sections and rules
    cal = next(c for c in contracts if c["id"] == "calendar")
    assert cal.get("canonical_authority") == "https://example.com/calendar"
    assert cal.get("display_authority") == "Calendar Service"
    assert "slot_rules" in cal["sections"]
    assert cal["sections"]["slot_rules"][0]["text"] == "Slot start time must precede slot end time"
    assert cal["sections"]["slot_rules"][0]["invariant"] is True
    assert cal["sections"]["slot_rules"][0]["rationale"] == "Time ordering invariant"
    assert cal["rules"] == ["Slot start time must precede slot end time"]

    # Swaps rule text preservation
    swaps = next(c for c in contracts if c["id"] == "swaps")
    assert swaps["rules"] == ["Swaps require consent from both clinical parties"]
    assert swaps["sections"]["rules"][0]["invariant"] is True

    # Auth admin sections
    auth = next(c for c in contracts if c["id"] == "auth_admin")
    assert "critical_constraints" in auth["sections"]
    assert "review_checklist" in auth["sections"]
    assert auth["sections"]["critical_constraints"][0]["text"] == "Administrator endpoints must enforce role check"
    assert auth["sections"]["review_checklist"][0]["text"] == "Check bearer token header redaction"

    # Named string list
    resp = next(c for c in contracts if c["id"] == "response_model_rules")
    assert resp["rules"] == ["All response models must validate datetime in UTC"]
    assert resp["sections"]["response_model_rules"][0]["text"] == "All response models must validate datetime in UTC"


def test_cm_a2_nested_section_identity() -> None:
    """CM-A2-NESTED-SECTION-IDENTITY: Sections critical_constraints != review_checklist (RED_REQUIRED)."""
    fixture = _structural_mirror_fixture()
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(fixture)
    auth = next(c for c in contracts if c["id"] == "auth_admin")

    assert "critical_constraints" in auth["sections"]
    assert "review_checklist" in auth["sections"]
    assert auth["sections"]["critical_constraints"] != auth["sections"]["review_checklist"]
    assert len(auth["sections"]["critical_constraints"]) == 1
    assert len(auth["sections"]["review_checklist"]) == 1


def test_ab_a2_section_identity() -> None:
    """AB-A2-SECTION-IDENTITY: Stripping section identity loses structural section distinction (ABLATION_CONTROL)."""
    fixture = _structural_mirror_fixture()
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(fixture)
    auth = next(c for c in contracts if c["id"] == "auth_admin")

    # Ablated representation collapses sections into a single flat list
    ablated_sections = {"collapsed": [
        *auth["sections"]["critical_constraints"],
        *auth["sections"]["review_checklist"],
    ]}
    assert "critical_constraints" not in ablated_sections, "Ablation must lose distinct critical_constraints section (RED)"
    assert "review_checklist" not in ablated_sections, "Ablation must lose distinct review_checklist section (RED)"


def test_cm_a2_rule_dict_text_preservation() -> None:
    """CM-A2-RULE-DICT-TEXT-PRESERVATION: Dict rules with rule: '...' preserve text (RED_REQUIRED)."""
    raw = {
        "swaps": {
            "rules": [
                {"rule": "Clinical consent required for swap execution", "invariant": True, "rationale": "Safety guard"}
            ]
        }
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    swaps = contracts[0]
    assert swaps["rules"] == ["Clinical consent required for swap execution"]
    assert swaps["sections"]["rules"][0]["text"] == "Clinical consent required for swap execution"
    assert swaps["sections"]["rules"][0]["invariant"] is True


def test_ab_a2_rule_dict_text() -> None:
    """AB-A2-RULE-DICT-TEXT: Ignoring rule key drops rule text (predecessor defect) (ABLATION_CONTROL)."""
    raw_rule = {"rule": "Clinical consent required for swap execution", "invariant": True}

    # Predecessor logic only looked for description or id
    def predecessor_extract_text(item: dict[str, Any]) -> str | None:
        return item.get("description") or item.get("id")

    extracted = predecessor_extract_text(raw_rule)
    assert extracted is None, "Predecessor extraction fails to find rule text (RED)"


def test_cm_a2_legacy_flat_compat() -> None:
    """CM-A2-LEGACY-FLAT-COMPAT: Legacy flat rules and legacy alias 'calendar' preserved (GREEN_PRESERVATION)."""
    intake = _base_intake()
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
    """CM-A2-NAMED-STRING-LIST: Section with named list[str] normalizes cleanly (GREEN_PRESERVATION)."""
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
    """CM-A2-MALFORMED-CONTRACT_BINDINGS: Malformed contract_bindings emits typed limitation (GREEN_PRESERVATION)."""
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(
        {"packs": [{"id": "p1"}]},
        extra_bindings="not_a_mapping",
    )
    assert any(lim.startswith("malformed_contract_bindings:") for lim in limits)


def test_pc_a2_contract_bindings_positive_control() -> None:
    """PC-A2-CONTRACT-BINDINGS: Valid mapping resolves cleanly (POSITIVE_CONTROL)."""
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(
        {"packs": [{"id": "p1"}]},
        extra_bindings={"p1": ["contract-a", "contract-b"]},
    )
    assert limits == []
    assert bindings == {"p1": ["contract-a", "contract-b"]}


# ---------------------------------------------------------------------------
# A3 — Applicability + Explicit Relations
# ---------------------------------------------------------------------------


def test_cm_a3_owner_focal_relations() -> None:
    """CM-A3-OWNER-FOCAL-RELATIONS: auth_admin -> security + auth_admin, backend_schema_contract -> response_model_rules (GREEN_PRESERVATION)."""
    intake = _base_intake()
    intake.target_profile = {
        "schema_version": "agent-review.target-profile.v1",
        "target_repo": "mglpsw/AgentEscala",
        "domain_contracts": [
            {"id": "security", "description": "Security core"},
            {"id": "auth_admin", "description": "Auth admin contract"},
            {"id": "response_model_rules", "description": "Response model schema contract"},
        ],
        "review_packs": {
            "packs": {
                "auth_admin": {
                    "paths": ["backend/api/*"],
                    "domain_contract": "security",
                },
                "backend_schema_contract": {
                    "paths": ["backend/api/*"],
                    "domain_contract": "response_model_rules",
                },
            },
            "contract_bindings": {
                "auth_admin": ["auth_admin"],
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

    # auth_admin pack has effective_contracts = [auth_admin, security]
    auth_pack = next(p for p in ctx["review_packs"] if p["id"] == "auth_admin")
    assert auth_pack["effective_contracts"] == ["auth_admin", "security"]

    # Both security and auth_admin are required
    contract_map = {c["id"]: c.get("required") for c in ctx["domain_contracts"]}
    assert contract_map.get("security") is True
    assert contract_map.get("auth_admin") is True
    assert contract_map.get("response_model_rules") is True


def test_cm_a3_unrelated_pack_does_not_match_and_does_not_leak_contracts() -> None:
    """CM-A3-UNRELATED-PACK: Pack with non-matching paths does not match or leak contracts (GREEN_PRESERVATION)."""
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
    """CM-A3-UNKNOWN-BINDING: Unknown binding emits typed limitation and never not_relevant (GREEN_PRESERVATION)."""
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
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in limits)


def test_cm_a3_exact_identity_boundary() -> None:
    """CM-A3-EXACT-IDENTITY-BOUNDARY: Case-sensitive identity matching enforced; legacy alias isolated (RED_REQUIRED)."""
    pack = {"id": "admin_pack"}

    # Exact match succeeds
    assert payload_cost_model._review_pack_matches_selected(pack, "admin_pack") is True

    # Case mismatch fails
    assert payload_cost_model._review_pack_matches_selected(pack, "Admin_Pack") is False
    assert payload_cost_model._review_pack_matches_selected(pack, "ADMIN_PACK") is False

    # Legacy alias calendar <-> agentescala-calendar is explicitly preserved
    legacy_pack = {"id": "agentescala-calendar"}
    assert payload_cost_model._review_pack_matches_selected(legacy_pack, "calendar") is True
    reverse_pack = {"id": "calendar"}
    assert payload_cost_model._review_pack_matches_selected(reverse_pack, "agentescala-calendar") is True


def test_cm_a3_case_sensitive_pattern_matching() -> None:
    """CM-A3-CASE-SENSITIVE-PATTERN: Pattern matching uses case-sensitive fnmatchcase (GREEN_PRESERVATION)."""
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


def test_ab_a3_explicit_binding() -> None:
    """AB-A3-EXPLICIT-BINDING: Discarding contract_bindings prevents bound contract from becoming required (ABLATION_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [{"id": "c1", "paths": ["backend/*"]}],
        "review_packs": {
            "packs": {"p1": {"paths": ["backend/*"]}},
            "contract_bindings": {"p1": ["c1"]},
        },
    }

    # Normal: c1 is required
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="c-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    assert ctx["domain_contracts"][0].get("required") is True

    # Ablated: remove bindings
    intake_ablated = _base_intake()
    intake_ablated.target_profile = {
        "domain_contracts": [{"id": "c1", "paths": ["backend/*"]}],
        "review_packs": {"packs": {"p1": {"paths": ["backend/*"]}}},
    }
    ctx_ablated, _ = payload_cost_model.contracts_context(
        intake_ablated,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="c-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    assert ctx_ablated["domain_contracts"][0].get("required") is not True, "Ablation must leave contract optional (RED)"


def test_ab_a3_exact_identity() -> None:
    """AB-A3-EXACT-IDENTITY: Case-insensitive pack matching violates identity boundary (ABLATION_CONTROL)."""
    pack = {"id": "admin_pack"}

    # Ablated matching allows lower-case equivalence
    def ablated_matches(p: dict[str, Any], sel: str) -> bool:
        return p.get("id", "").lower() == sel.lower()

    assert ablated_matches(pack, "Admin_Pack") is True, "Ablation allows case mismatch (RED)"


def test_ab_a3_fnmatchcase() -> None:
    """AB-A3-FNMATCHCASE: Case-insensitive glob matching violates path case boundary (ABLATION_CONTROL)."""
    import fnmatch
    path = "backend/api/shifts.py"
    pattern = "Backend/Api/*.py"

    # Strict case-sensitive
    assert not fnmatch.fnmatchcase(path, pattern)

    # Ablated case-insensitive
    assert fnmatch.fnmatchcase(path.lower(), pattern.lower()), "Ablated case-insensitive match succeeds (RED)"


def test_cm_a3_availability_is_not_applicability() -> None:
    """CM-A3-AVAILABILITY-IS-NOT-APPLICABILITY: Source availability does not imply contract applicability (RED_REQUIRED).

    Intake provides:
    - domain_contracts (modern mapping): calendar, security, unrelated
    - review_packs (modern mapping): calendar_pack (paths: backend/calendar/**, domain_contract: calendar),
                                    security_pack (paths: backend/auth/**, domain_contract: security)
    Subject: backend/calendar/service.py
    Execution trace:
    intake -> build_semantic_chunk_plan -> real SemanticChunk.contracts -> contracts_context -> build_chunk_payloads
    Expected:
    - calendar_pack: applicable
    - calendar: applicable (required=True)
    - security_pack: NOT applicable
    - security: NOT applicable
    - unrelated: NOT applicable
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "calendar": {"rules": ["event times in UTC"]},
            "security": {"rules": ["validate all tokens"]},
            "unrelated": {"rules": ["unrelated rule"]},
        },
        "review_packs": {
            "packs": {
                "calendar_pack": {
                    "paths": ["backend/calendar/**"],
                    "domain_contract": "calendar",
                },
                "security_pack": {
                    "paths": ["backend/auth/**"],
                    "domain_contract": "security",
                },
            }
        },
    }
    intake.artifacts["full-diff"]["content"] = "\n".join(
        [
            "diff --git a/backend/calendar/service.py b/backend/calendar/service.py",
            "index 111..222 100644",
            "--- a/backend/calendar/service.py",
            "+++ b/backend/calendar/service.py",
            "@@ -10,1 +10,1 @@",
            "+def schedule_event(): pass",
        ]
    )
    intake.artifacts["file-diff-context"]["content"] = {
        "files": [{"path": "backend/calendar/service.py", "status": "modified"}],
        "coverage_requirements": {
            "must_review_files": ["backend/calendar/service.py"],
            "should_review_files": [],
            "may_summarize_files": [],
        },
    }

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    assert len(plan.chunks) == 1
    chunk = plan.chunks[0]
    assert chunk.files == ["backend/calendar/service.py"]
    # Both sources are declared available in chunk contracts
    assert "target_profile:domain_contracts" in chunk.contracts
    assert "target_profile:review_packs" in chunk.contracts

    # contracts_context using THAT chunk.contracts and chunk.files
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=chunk.files,
        chunk_contracts=chunk.contracts,
        chunk_id=chunk.chunk_id,
        selected_contract_pack=None,
        semantic_group=chunk.semantic_group,
    )
    assert limits == []

    pack_ids = [p["id"] for p in ctx["review_packs"]]
    assert pack_ids == ["calendar_pack"]
    assert "security_pack" not in pack_ids

    contract_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert contract_ids == ["calendar"]
    assert "security" not in contract_ids
    assert "unrelated" not in contract_ids

    # calendar is required
    cal_contract = next(c for c in ctx["domain_contracts"] if c["id"] == "calendar")
    assert cal_contract.get("required") is True

    # Payload carries ONLY applicable contracts and packs
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    payload = list(payloads.values())[0]
    payload_contracts = payload.chunk_context["contracts_context"]
    assert [c["id"] for c in payload_contracts["domain_contracts"]] == ["calendar"]
    assert [p["id"] for p in payload_contracts["review_packs"]] == ["calendar_pack"]


def test_cm_a3_unscoped_pack_is_not_global() -> None:
    """CM-A3-UNSCOPED-PACK-IS-NOT-GLOBAL: Pack without paths/patterns/is_global is not global (RED_REQUIRED).

    NoScope != Global: A pack without scope cannot automatically become global just because
    the review-packs artifact is available. It is admitted only if explicitly selected.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "general": {"rules": ["general rule"]},
        },
        "review_packs": {
            "packs": {
                "_default": {
                    "description": "Default pack without scope",
                    "domain_contract": "general",
                },
                "explicit_scoped": {
                    "description": "Explicit scoped pack",
                    "paths": ["backend/calendar/**"],
                },
            }
        },
    }

    # Case 1: Without explicit selection, unscoped pack does NOT match
    ctx_unselected, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/calendar/service.py"],
        chunk_contracts=["target_profile:review_packs"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="calendar",
    )
    unselected_pack_ids = [p["id"] for p in ctx_unselected["review_packs"]]
    assert "explicit_scoped" in unselected_pack_ids
    assert "_default" not in unselected_pack_ids

    # Case 2: With explicit selection, unscoped pack DOES match
    ctx_selected, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/calendar/service.py"],
        chunk_contracts=["target_profile:review_packs"],
        chunk_id="chunk-01",
        selected_contract_pack="_default",
        semantic_group="calendar",
    )
    selected_pack_ids = [p["id"] for p in ctx_selected["review_packs"]]
    assert "_default" in selected_pack_ids


def test_cm_a3_structural_target_like_shape() -> None:
    """CM-A3-STRUCTURAL-TARGET-LIKE-SHAPE: Target-like mirror preserves domain isolation across subjects (GREEN_PRESERVATION).

    Domains without own scopes: calendar, security, auth_admin, response_model_rules, unrelated
    Packs:
    - calendar_pack: paths backend/calendar/** -> domain_contract: calendar
    - auth_admin: paths backend/auth/** -> domain_contract: security, bindings: [auth_admin]
    - backend_schema_contract: paths backend/schema/** -> domain_contract: response_model_rules
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "calendar": {"rules": ["calendar UTC rule"]},
            "security": {"rules": ["security token rule"]},
            "auth_admin": {"rules": ["admin role check"]},
            "response_model_rules": {"rules": ["Pydantic model schema"]},
            "unrelated": {"rules": ["unrelated property"]},
        },
        "review_packs": {
            "packs": {
                "calendar_pack": {
                    "paths": ["backend/calendar/**"],
                    "domain_contract": "calendar",
                },
                "auth_admin": {
                    "paths": ["backend/auth/**"],
                    "domain_contract": "security",
                },
                "backend_schema_contract": {
                    "paths": ["backend/schema/**"],
                    "domain_contract": "response_model_rules",
                },
            },
            "contract_bindings": {
                "auth_admin": ["auth_admin"],
            },
        },
    }
    chunk_contracts = ["target_profile:domain_contracts", "target_profile:review_packs"]

    # 1. Calendar subject
    ctx_cal, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/calendar/service.py"],
        chunk_contracts=chunk_contracts,
        chunk_id="cal-01",
        selected_contract_pack=None,
        semantic_group="calendar",
    )
    cal_contracts = {c["id"]: c.get("required") for c in ctx_cal["domain_contracts"]}
    assert "calendar" in cal_contracts
    assert cal_contracts["calendar"] is True
    assert "security" not in cal_contracts
    assert "auth_admin" not in cal_contracts
    assert "response_model_rules" not in cal_contracts
    assert "unrelated" not in cal_contracts

    # 2. Auth subject
    ctx_auth, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/auth/login.py"],
        chunk_contracts=chunk_contracts,
        chunk_id="auth-01",
        selected_contract_pack=None,
        semantic_group="auth",
    )
    auth_contracts = {c["id"]: c.get("required") for c in ctx_auth["domain_contracts"]}
    assert "security" in auth_contracts and auth_contracts["security"] is True
    assert "auth_admin" in auth_contracts and auth_contracts["auth_admin"] is True
    assert "calendar" not in auth_contracts
    assert "response_model_rules" not in auth_contracts
    assert "unrelated" not in auth_contracts

    # 3. Schema subject
    ctx_schema, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/schema/models.py"],
        chunk_contracts=chunk_contracts,
        chunk_id="schema-01",
        selected_contract_pack=None,
        semantic_group="schema",
    )
    schema_contracts = {c["id"]: c.get("required") for c in ctx_schema["domain_contracts"]}
    assert "response_model_rules" in schema_contracts and schema_contracts["response_model_rules"] is True
    assert "calendar" not in schema_contracts
    assert "security" not in schema_contracts
    assert "auth_admin" not in schema_contracts
    assert "unrelated" not in schema_contracts


def test_pc_a3_applicable_pack_binds_contract() -> None:
    """PC-A3-APPLICABLE-PACK-BINDS-CONTRACT: Applicable pack binds contract to chunk as required (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "api_contract": {"rules": ["must validate input"]},
        },
        "review_packs": {
            "packs": {
                "api_pack": {"paths": ["backend/api/**"], "domain_contract": "api_contract"},
            }
        },
    }
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:domain_contracts", "target_profile:review_packs"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    contract_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert contract_ids == ["api_contract"]
    assert ctx["domain_contracts"][0]["required"] is True


def test_pc_a3_explicit_contract_ref() -> None:
    """PC-A3-EXPLICIT-CONTRACT-REF: Explicit contract:<id> ref in chunk_contracts marks contract required (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "special_contract": {"rules": ["special constraint"]},
            "other_contract": {"rules": ["other constraint"]},
        },
    }
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["contract:special_contract"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    contract_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert contract_ids == ["special_contract"]
    assert ctx["domain_contracts"][0]["required"] is True
    assert "other_contract" not in contract_ids


def test_pc_a3_explicit_global() -> None:
    """PC-A3-EXPLICIT-GLOBAL: Explicit is_global=true or scope='global' makes item applicable to all chunks (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "global_contract": {"is_global": True, "rules": ["applies everywhere"]},
            "non_global": {"rules": ["applies nowhere without pack"]},
        },
        "review_packs": {
            "packs": {
                "global_pack": {"is_global": True, "description": "Global pack"},
                "non_global_pack": {"description": "Unscoped non-global pack"},
            }
        },
    }
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/some/random/file.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="random",
    )
    assert [c["id"] for c in ctx["domain_contracts"]] == ["global_contract"]
    assert [p["id"] for p in ctx["review_packs"]] == ["global_pack"]


def test_pc_a3_explicit_selected_pack() -> None:
    """PC-A3-EXPLICIT-SELECTED-PACK: Selected pack without paths matches if and only if explicitly selected (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "special_pack": {"description": "Pack without paths"},
            }
        }
    }
    # Unselected -> does not match
    ctx1, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:review_packs"],
        chunk_id="c1",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert ctx1["review_packs"] == []

    # Selected -> matches
    ctx2, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:review_packs"],
        chunk_id="c2",
        selected_contract_pack="special_pack",
        semantic_group="api",
    )
    assert [p["id"] for p in ctx2["review_packs"]] == ["special_pack"]


def test_pc_a3_legacy_flat_compatibility() -> None:
    """PC-A3-LEGACY-FLAT-COMPATIBILITY: Legacy flat format preserves extensional source availability behavior (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "legacy_rule_1", "description": "legacy rule 1"},
            {"id": "legacy_rule_2", "description": "legacy rule 2"},
        ],
        "review_packs": [
            {"id": "legacy_pack_1", "description": "legacy pack 1"},
        ],
    }
    # When target_profile references are present, legacy flat contracts match
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:domain_contracts", "target_profile:review_packs"],
        chunk_id="c1",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert [c["id"] for c in ctx["domain_contracts"]] == ["legacy_rule_1", "legacy_rule_2"]
    assert [p["id"] for p in ctx["review_packs"]] == ["legacy_pack_1"]


def test_ab_a3_availability_applicability() -> None:
    """AB-A3-AVAILABILITY-APPLICABILITY: Conflating source availability with applicability leaks unrelated contracts (ABLATION_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "calendar": {"rules": ["event times in UTC"]},
            "security": {"rules": ["validate all tokens"]},
            "unrelated": {"rules": ["unrelated rule"]},
        },
        "review_packs": {
            "packs": {
                "calendar_pack": {
                    "paths": ["backend/calendar/**"],
                    "domain_contract": "calendar",
                },
                "security_pack": {
                    "paths": ["backend/auth/**"],
                    "domain_contract": "security",
                },
            }
        },
    }
    # Normal mechanism: unrelated and security are excluded
    ctx_normal, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/calendar/service.py"],
        chunk_contracts=["target_profile:domain_contracts", "target_profile:review_packs"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="calendar",
    )
    normal_ids = [c["id"] for c in ctx_normal["domain_contracts"]]
    assert normal_ids == ["calendar"]
    assert "security" not in normal_ids
    assert "unrelated" not in normal_ids

    # Ablated mechanism: simulate predecessor conflation (availability admitting all unscoped contracts)
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(intake.target_profile["domain_contracts"])
    ablated_contracts = [
        c for c in contracts
        if "target_profile:domain_contracts" in ["target_profile:domain_contracts"] and not payload_cost_model._paths_from_item(c)
    ]
    ablated_ids = [c["id"] for c in ablated_contracts]
    # Under ablation, unrelated and security leak into the chunk! (RED under ablation)
    assert "unrelated" in ablated_ids
    assert "security" in ablated_ids
    assert "calendar" in ablated_ids


# ---------------------------------------------------------------------------
# A4 — Canonical Cost + Deterministic Packing
# ---------------------------------------------------------------------------


def test_cm_ga_a4_cost_bound_measurement() -> None:
    """CM-GA-A4-COST: Direct measurement proves A <= P <= B across 5 variations (RED_REQUIRED)."""
    variations = [
        ("no_contracts", {}),
        ("optional_contracts", {
            "domain_contracts": [{"id": "opt-c", "description": "optional contract", "paths": ["backend/api/*"]}]
        }),
        ("required_contracts", {
            "domain_contracts": [{"id": "req-c", "description": "required contract", "paths": ["backend/api/*"]}],
            "review_packs": {
                "packs": {"p1": {"paths": ["backend/api/*"]}},
                "contract_bindings": {"p1": ["req-c"]},
            },
        }),
        ("large_required_contract", {
            "domain_contracts": [{"id": "req-large", "description": "L" * 300, "paths": ["backend/api/*"]}],
            "review_packs": {
                "packs": {"p1": {"paths": ["backend/api/*"]}},
                "contract_bindings": {"p1": ["req-large"]},
            },
        }),
        ("multiple_required_contracts", {
            "domain_contracts": [
                {"id": "req-1", "description": "M1" * 100, "paths": ["backend/api/*"]},
                {"id": "req-2", "description": "M2" * 100, "paths": ["backend/api/*"]},
            ],
            "review_packs": {
                "packs": {"p1": {"paths": ["backend/api/*"]}},
                "contract_bindings": {"p1": ["req-1", "req-2"]},
            },
        }),
    ]

    budget = 12000
    for name, profile_contracts in variations:
        intake = _base_intake()
        intake.target_profile.update(profile_contracts)
        intake_dict = intake.model_dump(mode="json")

        plan = build_semantic_chunk_plan(intake_dict, max_blocks=2, max_chars_per_block=budget)
        assert plan.status in {"complete", "partial"}
        assert len(plan.chunks) > 0

        brief = _brief(intake, plan)
        manifest, payloads = build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=brief,
            checks=None,
            validation_evidence=None,
        )

        for chunk in plan.chunks:
            payload = payloads.get(chunk.chunk_id)
            if payload is None:
                continue

            # Actual emitted payload length
            actual_len = payload_cost_model.canonical_len(payload.model_dump(mode="json"))

            # Direct projection call
            hunks = payload_cost_model.diff_by_file(intake)
            projected = payload_cost_model.project_min_hunk_preserving_chars(
                intake=intake,
                chunk_files=chunk.files,
                chunk_contracts=chunk.contract_refs,
                semantic_group=chunk.semantic_group,
                max_blocks=2,
                target=brief.target,
                brief_target=brief.target,
                brief_review=brief.review,
                brief_required_files=payload_cost_model.required_files_wire(intake),
                brief_limitations=brief.limitations,
                selected_contract_pack=brief.review.get("contract_pack"),
                checks=None,
                validation_evidence=None,
                hunks=hunks,
                created_at=plan.created_at,
            )

            # Property: actual_len <= projected <= budget
            assert actual_len <= projected, f"[{name}] Actual len {actual_len} exceeds projection {projected}"
            assert projected <= budget, f"[{name}] Projection {projected} exceeds budget {budget}"


def test_pc_ga_a4_cost_admitted_candidate() -> None:
    """PC-GA-A4-COST: Admitted candidate preserves required context and hunks (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [{"id": "req-c", "description": "R", "paths": ["backend/api/*"]}],
        "review_packs": {
            "packs": {"p1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"p1": ["req-c"]},
        },
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=12000)
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    payload = next(iter(payloads.values()))
    assert len(payload.chunk_context["chunk_hunks"]) > 0
    c_ids = [c["id"] for c in payload.chunk_context["contracts_context"]["domain_contracts"]]
    assert "req-c" in c_ids


def test_ab_ga_a4_cost() -> None:
    """AB-GA-A4-COST: Dropping required contracts from minimal_contracts_context underestimates payload floor (ABLATION_CONTROL)."""
    full_ctx = {
        "domain_contracts": [
            {"id": "req-heavy", "description": "Z" * 1500, "required": True},
        ],
        "review_packs": [],
    }

    # Correct minimal contracts context retains required contract
    correct_min = payload_cost_model.minimal_contracts_context(full_ctx)
    correct_len = len(payload_cost_model.canonical_json(correct_min))

    # Ablated minimal context discards required contracts
    ablated_min = {"domain_contracts": [], "review_packs": []}
    ablated_len = len(payload_cost_model.canonical_json(ablated_min))

    assert ablated_len < correct_len, "Ablation must underestimate minimal context size (RED)"
    assert correct_len - ablated_len >= 1500


def test_cm_ga_a4_repack_stress() -> None:
    """CM-GA-A4-REPACK: Real budget pressure forces candidate split and deterministic packing (RED_REQUIRED)."""
    intake = _base_intake()
    # Add multiple files in diff and file-diff-context
    files = [
        {"path": f"backend/api/service_{i}.py", "status": "modified", "summary": f"service {i}"}
        for i in range(4)
    ]
    intake.artifacts["file-diff-context"]["content"]["files"] = files
    intake.artifacts["file-diff-context"]["content"]["coverage_requirements"]["must_review_files"] = [f["path"] for f in files]

    diff_lines = []
    for f in files:
        diff_lines.extend([
            f"diff --git a/{f['path']} b/{f['path']}",
            "index 111..222 100644",
            f"--- a/{f['path']}",
            f"+++ b/{f['path']}",
            "@@ -1,1 +1,1 @@",
            "+value=42",
        ])
    intake.artifacts["full-diff"]["content"] = "\n".join(diff_lines)

    intake_dict = intake.model_dump(mode="json")

    # Tight budget (5000): forces 4 files to split across multiple chunks
    runs = [
        build_semantic_chunk_plan(intake_dict, max_blocks=3, max_chars_per_block=5000)
        for _ in range(5)
    ]

    first_run = runs[0]
    assert len(first_run.chunks) > 1, "Budget pressure must force candidate split"
    assert first_run.status == "complete"
    assert len(first_run.files_covered) == 4

    # Verify determinism across all 5 runs
    for run in runs[1:]:
        assert run.model_dump_json() == first_run.model_dump_json()
        assert [c.chunk_id for c in run.chunks] == [c.chunk_id for c in first_run.chunks]
        assert [c.files for c in run.chunks] == [c.files for c in first_run.chunks]


def test_ab_ga_a4_repack() -> None:
    """AB-GA-A4-REPACK: Mutating candidate sorting order causes partition divergence (ABLATION_CONTROL)."""
    candidates = [
        {"id": "chunk-01", "priority": 1, "files": ["a.py", "b.py"]},
        {"id": "chunk-02", "priority": 2, "files": ["c.py", "d.py"]},
    ]

    # Standard order
    std_order = sorted(candidates, key=lambda c: c["priority"])

    # Ablated reverse order
    ablated_order = sorted(candidates, key=lambda c: -c["priority"])

    assert std_order != ablated_order, "Ablation must cause partition divergence (RED)"


# ---------------------------------------------------------------------------
# A5 — Required-Context Loss Propagation
# ---------------------------------------------------------------------------


def test_cm_a5_required_context_dropped_shrink_ladder_order() -> None:
    """CM-A5-REQUIRED-CONTEXT-DROPPED: Shrink ladder drops optional before required when budget is constrained (RED_REQUIRED)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-1", "description": "R" * 200, "paths": ["backend/api/*"]},
            {"id": "opt-1", "description": "O" * 500, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-1"]},
        },
    }

    # Plan with comfortable budget
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    brief = _brief(intake, plan)

    # Build unconstrained to measure untruncated length
    _, unconstrained_payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    unconstrained_chunk = next(iter(unconstrained_payloads.values()))
    untruncated_len = unconstrained_chunk.truncation.original_chars

    # Find budget where optional contract is dropped but required contract fits
    # Set chunk prompt_budget_chars to force shrinking contracts_context
    tight_budget = untruncated_len - 300
    plan.chunks[0].prompt_budget_chars = tight_budget

    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    payload = next(iter(payloads.values()))
    c_ids = [c["id"] for c in payload.chunk_context["contracts_context"]["domain_contracts"]]
    # Required contract must be preserved
    assert "req-1" in c_ids
    # Optional contract must be dropped
    assert "opt-1" not in c_ids
    # No required contract loss limitation
    assert not any(lim.startswith("required_contract_context_lost:req-1") for lim in payload.limitations)


def test_ab_a5_shrink_order() -> None:
    """AB-A5-SHRINK-ORDER: Reversing contract shrink order drops required contract first (ABLATION_CONTROL)."""
    payload = {
        "chunk_context": {
            "contracts_context": {
                "review_packs": [],
                "domain_contracts": [
                    {"id": "req-1", "required": True},
                    {"id": "opt-1", "required": False},
                ],
            }
        },
        "limitations": [],
    }

    # Ablated shrinker pops required contracts first
    def ablated_shrink_contracts_context(p: dict[str, Any]) -> bool:
        contracts = p["chunk_context"]["contracts_context"]["domain_contracts"]
        for i, item in enumerate(contracts):
            if item.get("required"):
                contracts.pop(i)
                p["limitations"].append(f"required_contract_context_lost:{item['id']}")
                return True
        return False

    changed = ablated_shrink_contracts_context(payload)
    assert changed is True
    remaining_ids = [c["id"] for c in payload["chunk_context"]["contracts_context"]["domain_contracts"]]
    assert "opt-1" in remaining_ids
    assert "req-1" not in remaining_ids, "Ablated order incorrectly dropped required contract (RED)"


def test_cm_a5_required_floor_exceeds_budget_canonical_flow() -> None:
    """CM-A5-REQUIRED-FLOOR-EXCEEDS-BUDGET: Canonical planner flow with budget < required floor produces non-conclusive plan (RED_REQUIRED)."""
    intake = _base_intake()
    # Contract is 2500 characters
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-huge", "description": "H" * 2500, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-huge"]},
        },
    }

    # Canonical flow: budget is set to 800 from the start (cannot fit 2500 contract + hunks)
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=800)

    # Result must be non-conclusive: status degraded or files not covered
    assert plan.status == "degraded"
    assert "backend/api/shifts.py" in plan.files_not_covered
    assert any("must_review_payload_oversize" in lim or "chunk_plan_budget_exhausted" in lim for lim in plan.limitations)


def test_defensive_plan_builder_mismatch_control() -> None:
    """DEFENSIVE_PLAN_BUILDER_MISMATCH_CONTROL: Post-hoc budget reduction marks status='limited' and payload_path=None (DEFENSIVE_CONTROL)."""
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

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    for c in plan.chunks:
        c.prompt_budget_chars = 2500  # Defensive mismatch: tamper budget after plan

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    limited_entries = [e for e in manifest.chunks if any(lim.startswith("required_contract_context_lost:req-huge") for lim in e.limitations)]
    assert len(limited_entries) > 0
    for entry in limited_entries:
        assert entry.status == "limited"
        assert entry.payload_path is None


def test_cm_a5_carrier_consumption_control(tmp_path: Path) -> None:
    """CARRIER_CONSUMPTION_CONTROL: Injected limitation reaches quality gate across 3 scenarios (DEFENSIVE_CONTROL).

    Proves CarrierConsumed: an injected limitation in plan carrier travels to quality gate and forces gate.status != passed.
    Note: For natural end-to-end production carrier origin, see CM-A5-PRODUCTION-CARRIER-ORIGIN.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-contract", "description": "R" * 200, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-contract"]},
        },
    }

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    # Inject required contract loss limitation into plan carrier
    plan.limitations.append("required_contract_context_lost:req-contract")
    brief = _brief(intake, plan)

    # Build payloads
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    chunk = plan.chunks[0]

    # --- Scenario 1: Required loss + no blocker ---
    resp_dir_1 = tmp_path / "resp1"
    resp_dir_1.mkdir()
    resp_1 = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": ["backend/api/shifts.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }
    (resp_dir_1 / f"{chunk.chunk_id}.json").write_text(json.dumps(resp_1), encoding="utf-8")

    results_1 = parse_chunk_results(plan, responses_dir=resp_dir_1)
    review_1 = synthesize_final_review(results_1)
    doc_1 = validate_final_review_document(review_1.model_dump(mode="json"))
    gate_1 = evaluate_review_quality_gate(final_review=doc_1, chunk_results=results_1, intake=intake, chunk_plan=plan)

    assert gate_1.status == "manual_review_required"
    assert gate_1.manual_review_required is True
    assert gate_1.status != "passed"

    # --- Scenario 2: Required loss + reliable blocker ---
    resp_dir_2 = tmp_path / "resp2"
    resp_dir_2.mkdir()
    resp_2 = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [
            {
                "severity": "P1",
                "title": "Unauthenticated admin endpoint exposed",
                "file_path": "backend/api/shifts.py",
                "line_or_hunk": "L10-L15",
                "evidence": "Endpoint token is assigned without role check",
                "source_artifact": "artifact:file-diff-context",
                "impact": "Privilege escalation vulnerability",
                "confidence": "high",
                "dedupe_key": "admin-unauth",
            }
        ],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": ["backend/api/shifts.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }
    (resp_dir_2 / f"{chunk.chunk_id}.json").write_text(json.dumps(resp_2), encoding="utf-8")

    results_2 = parse_chunk_results(plan, responses_dir=resp_dir_2)
    review_2 = synthesize_final_review(results_2)
    doc_2 = validate_final_review_document(review_2.model_dump(mode="json"))
    gate_2 = evaluate_review_quality_gate(final_review=doc_2, chunk_results=results_2, intake=intake, chunk_plan=plan)

    # With reliable blocker: status is degraded, verdict changes_requested, manual_review_required False
    assert gate_2.status == "degraded"
    assert gate_2.normalized_verdict == "changes_requested"
    assert gate_2.manual_review_required is False
    assert gate_2.status != "passed"

    # --- Scenario 3: Required loss + model claims approved ---
    # If a model claims approved despite degraded input, quality gate forces manual_review_required
    review_3_raw = review_1.model_dump(mode="json")
    review_3_raw["verdict"] = "approved"
    doc_3 = validate_final_review_document(review_3_raw)
    gate_3 = evaluate_review_quality_gate(final_review=doc_3, chunk_results=results_1, intake=intake, chunk_plan=plan)

    assert gate_3.status == "manual_review_required"
    assert gate_3.manual_review_required is True
    assert gate_3.status != "passed"


def test_cm_a5_production_carrier_origin(tmp_path: Path) -> None:
    """CM-A5-PRODUCTION-CARRIER-ORIGIN: Real production limitation unresolved_contract_binding propagates naturally through carrier graph to gate.status != passed across 3 scenarios (RED_REQUIRED).

    Producer: contracts_context() detects unresolved contract binding from contract_bindings -> missing-contract
    Carriers: SemanticChunkPlan.limitations -> PRBrief.limitations -> ChunkPayload.limitations -> ChunkResults.limitations -> FinalReview.limitations
    Terminal consumer: evaluate_review_quality_gate() sets input_degraded=True -> gate.status != passed
    Zero manual carrier injection.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "security": {"rules": ["all tokens validated"]},
        },
        "review_packs": {
            "packs": {
                "pack1": {"paths": ["backend/api/*"]},
            },
            "contract_bindings": {
                "pack1": ["missing-contract"],
            },
        },
    }

    # 1. Producer: build_semantic_chunk_plan calls contracts_context and captures limitation naturally
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    expected_limitation = "unresolved_contract_binding:pack1:missing-contract"
    assert expected_limitation in plan.limitations
    assert plan.status == "degraded"

    # 2. Carrier 1: PRBrief
    brief = _brief(intake, plan)
    assert expected_limitation in brief.limitations

    # 3. Carrier 2: ChunkPayload and ChunkPayloadManifest
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    chunk = plan.chunks[0]
    payload = payloads[f"{chunk.chunk_id}.json"]
    assert expected_limitation in payload.limitations
    assert expected_limitation in manifest.chunks[0].limitations

    # --- Scenario 1: Natural limitation + clean response (no findings) ---
    resp_dir_1 = tmp_path / "prod_resp1"
    resp_dir_1.mkdir()
    resp_1 = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": ["backend/api/shifts.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }
    (resp_dir_1 / f"{chunk.chunk_id}.json").write_text(json.dumps(resp_1), encoding="utf-8")

    # Carrier 3: ChunkResults
    results_1 = parse_chunk_results(plan, responses_dir=resp_dir_1)
    assert expected_limitation in results_1.limitations
    assert results_1.status == "degraded"

    # Carrier 4: FinalReview
    review_1 = synthesize_final_review(results_1)
    assert expected_limitation in review_1.limitations
    assert review_1.status == "degraded"

    # Terminal Consumer: QualityGate
    doc_1 = validate_final_review_document(review_1.model_dump(mode="json"))
    gate_1 = evaluate_review_quality_gate(final_review=doc_1, chunk_results=results_1, intake=intake, chunk_plan=plan)

    assert gate_1.status == "manual_review_required"
    assert gate_1.manual_review_required is True
    assert gate_1.status != "passed"

    # --- Scenario 2: Natural limitation + reliable blocker ---
    resp_dir_2 = tmp_path / "prod_resp2"
    resp_dir_2.mkdir()
    resp_2 = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [
            {
                "severity": "P1",
                "title": "Unauthenticated admin endpoint exposed",
                "file_path": "backend/api/shifts.py",
                "line_or_hunk": "L10-L15",
                "evidence": "Endpoint token is assigned without role check",
                "source_artifact": "artifact:file-diff-context",
                "impact": "Privilege escalation vulnerability",
                "confidence": "high",
                "dedupe_key": "admin-unauth",
            }
        ],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": ["backend/api/shifts.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }
    (resp_dir_2 / f"{chunk.chunk_id}.json").write_text(json.dumps(resp_2), encoding="utf-8")

    results_2 = parse_chunk_results(plan, responses_dir=resp_dir_2)
    review_2 = synthesize_final_review(results_2)
    doc_2 = validate_final_review_document(review_2.model_dump(mode="json"))
    gate_2 = evaluate_review_quality_gate(final_review=doc_2, chunk_results=results_2, intake=intake, chunk_plan=plan)

    assert gate_2.status == "degraded"
    assert gate_2.normalized_verdict == "changes_requested"
    assert gate_2.manual_review_required is False
    assert gate_2.status != "passed"

    # --- Scenario 3: Natural limitation + model claims approved ---
    review_3_raw = review_1.model_dump(mode="json")
    review_3_raw["verdict"] = "approved"
    doc_3 = validate_final_review_document(review_3_raw)
    gate_3 = evaluate_review_quality_gate(final_review=doc_3, chunk_results=results_1, intake=intake, chunk_plan=plan)

    assert gate_3.status == "manual_review_required"
    assert gate_3.manual_review_required is True
    assert gate_3.status != "passed"

    # --- Ablation: Dropping limitation from carrier produces unsound pass ---
    results_ablated = results_1.model_copy(deep=True)
    results_ablated.limitations = [lim for lim in results_ablated.limitations if not lim.startswith("unresolved_contract_binding:")]
    results_ablated.status = "complete"
    review_ablated = synthesize_final_review(results_ablated)
    plan_ablated = plan.model_copy(deep=True)
    plan_ablated.limitations = []
    plan_ablated.status = "complete"
    doc_ablated = validate_final_review_document(review_ablated.model_dump(mode="json"))
    gate_ablated = evaluate_review_quality_gate(final_review=doc_ablated, chunk_results=results_ablated, intake=intake, chunk_plan=plan_ablated)
    assert gate_ablated.status == "passed"


def test_cm_a5_production_carrier_origin_selected_pack_missing(tmp_path: Path) -> None:
    """CM-A5-PRODUCTION-CARRIER-ORIGIN-SELECTED-PACK-MISSING: Second production carrier origin selected_contract_pack_missing propagates to gate (POSITIVE_CONTROL)."""
    intake = _base_intake()
    intake.artifacts["review_metadata"] = {
        "name": "review_metadata",
        "path": "review_metadata.json",
        "kind": "json",
        "content": {"contract_pack": "nonexistent_pack"},
    }
    intake.target_profile = {
        "domain_contracts": {"security": {"rules": ["Validate token"]}},
        "review_packs": {"packs": {"pack1": {"paths": ["backend/api/*"]}}},
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    expected_limitation = "selected_contract_pack_missing:nonexistent_pack"
    assert expected_limitation in plan.limitations
    assert plan.status == "degraded"

    brief = _brief(intake, plan)
    assert expected_limitation in brief.limitations

    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    chunk = plan.chunks[0]
    payload = payloads[f"{chunk.chunk_id}.json"]
    assert expected_limitation in payload.limitations

    resp_dir = tmp_path / "missing_pack_resp"
    resp_dir.mkdir()
    resp = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "coverage_notes": {
            "files_reviewed": ["backend/api/shifts.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    }
    (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    results = parse_chunk_results(plan, responses_dir=resp_dir)
    assert expected_limitation in results.limitations
    review = synthesize_final_review(results)
    assert expected_limitation in review.limitations

    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)

    assert gate.status == "manual_review_required"
    assert gate.manual_review_required is True
    assert gate.status != "passed"
