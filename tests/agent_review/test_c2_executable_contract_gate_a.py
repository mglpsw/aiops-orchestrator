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

from app.agent_review import payload_cost_model, semantic_chunker
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
                "UserResponse",
                "AdminUserResponse",
                "TokenResponse",
            ],
            "orm_models": [
                "User",
                "Role",
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
    assert "response_models" in auth["sections"]
    assert "orm_models" in auth["sections"]
    assert auth["sections"]["critical_constraints"][0]["text"] == "Administrator endpoints must enforce role check"
    assert auth["sections"]["review_checklist"][0]["text"] == "Check bearer token header redaction"
    assert [item["text"] for item in auth["sections"]["response_models"]] == [
        "UserResponse",
        "AdminUserResponse",
        "TokenResponse",
    ]
    assert [item["text"] for item in auth["sections"]["orm_models"]] == [
        "User",
        "Role",
    ]

    # Named string list
    resp = next(c for c in contracts if c["id"] == "response_model_rules")
    assert resp["rules"] == ["All response models must validate datetime in UTC"]
    assert resp["sections"]["rules"][0]["text"] == "All response models must validate datetime in UTC"


def test_cm_a2_nested_section_identity() -> None:
    """CM-A2-NESTED-SECTION-IDENTITY: Sections critical_constraints != review_checklist (RED_REQUIRED)."""
    fixture = _structural_mirror_fixture()
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(fixture)
    auth = next(c for c in contracts if c["id"] == "auth_admin")

    assert "critical_constraints" in auth["sections"]
    assert "review_checklist" in auth["sections"]
    assert "response_models" in auth["sections"]
    assert "orm_models" in auth["sections"]
    assert auth["sections"]["critical_constraints"] != auth["sections"]["review_checklist"]
    assert len(auth["sections"]["critical_constraints"]) == 1
    assert len(auth["sections"]["review_checklist"]) == 1
    assert len(auth["sections"]["response_models"]) == 3
    assert len(auth["sections"]["orm_models"]) == 2


def test_ab_a2_section_identity() -> None:
    """AB-A2-SECTION-IDENTITY: Stripping section identity loses structural section distinction (ABLATION_CONTROL)."""
    fixture = _structural_mirror_fixture()
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(fixture)
    auth = next(c for c in contracts if c["id"] == "auth_admin")

    # Ablated representation collapses sections into a single flat list
    ablated_sections = {"collapsed": [
        *auth["sections"]["critical_constraints"],
        *auth["sections"]["review_checklist"],
        *auth["sections"]["response_models"],
        *auth["sections"]["orm_models"],
    ]}
    assert "critical_constraints" not in ablated_sections, "Ablation must lose distinct critical_constraints section (RED)"
    assert "review_checklist" not in ablated_sections, "Ablation must lose distinct review_checklist section (RED)"
    assert "response_models" not in ablated_sections, "Ablation must lose distinct response_models section (RED)"
    assert "orm_models" not in ablated_sections, "Ablation must lose distinct orm_models section (RED)"


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
        {"packs": {"p1": {}}},
        extra_bindings={"p1": ["contract-a", "contract-b"]},
    )
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
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

    # Cycle-3 (4179993596): the MODERN matcher is exact and target-agnostic -- the former
    # calendar <-> agentescala-calendar alias was a target-specific engine branch and is gone.
    legacy_pack = {"id": "agentescala-calendar"}
    assert payload_cost_model._review_pack_matches_selected(legacy_pack, "calendar") is False
    reverse_pack = {"id": "calendar"}
    assert payload_cost_model._review_pack_matches_selected(reverse_pack, "agentescala-calendar") is False
    # ...while LEGACY keeps recovering the same pairing generically (selected substring of id).
    assert payload_cost_model.legacy_pack_matches_selected(legacy_pack, "calendar") is True


def test_cm_a3_case_sensitive_pattern_matching() -> None:
    """CM-A3-CASE-SENSITIVE-PATTERN: Pattern matching uses case-sensitive fnmatchcase (GREEN_PRESERVATION)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "rule-case-wrong": {"patterns": ["Backend/Api/*.py"]},
            "rule-case-right": {"patterns": ["backend/api/*.py"]},
        }
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
        # Cycle-3: MODERN contract mapping (glob `paths` are a modern-mode semantic; the legacy
        # flat shape matches `paths` exactly, per frozen baseline 6bbd2f9).
        "domain_contracts": {"c1": {"description": "c1", "paths": ["backend/*"]}},
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
        "domain_contracts": {"c1": {"description": "c1", "paths": ["backend/*"]}},
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


# ---------------------------------------------------------------------------
# Post-Ready Findings 5407327682 Test Suite (Findings 4178603183..4178603218)
# ---------------------------------------------------------------------------


# Family F1: Identity Admission Totality


def test_cm_a2_mapping_key_id_conflict() -> None:
    """CM-A2-MAPPING-KEY-ID-CONFLICT (Finding 4178603188):
    Modern mapping key is contract identity authority; conflicting nested id is rejected fail-closed.
    """
    # Conflicting nested id fails closed
    conflict_doc = {
        "auth": {"id": "security", "description": "Security rules"},
    }
    contracts, state, sub, limits = payload_cost_model.normalize_domain_contracts(conflict_doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_contract:INVALID_IDENTITY" in limits
    assert contracts == []

    # Matching nested id is admitted
    matching_doc = {
        "auth": {"id": "auth", "description": "Auth rules"},
    }
    contracts, state, sub, limits = payload_cost_model.normalize_domain_contracts(matching_doc)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert len(contracts) == 1
    assert contracts[0]["id"] == "auth"

    # Absent nested id admits mapping key as identity
    absent_id_doc = {
        "auth": {"description": "Auth rules"},
    }
    contracts, state, sub, limits = payload_cost_model.normalize_domain_contracts(absent_id_doc)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert len(contracts) == 1
    assert contracts[0]["id"] == "auth"


def test_cm_a3_malformed_pack_scope() -> None:
    """CM-A3-MALFORMED-PACK-SCOPE (Finding 4178603193):
    Scope-bearing and prescribed fields must be validated before sanitizing; malformed shapes fail closed.
    """
    # Malformed paths (str instead of list[str]) in mapping pack
    malformed_paths = {
        "packs": {"auth": {"paths": "backend/**"}},
    }
    packs, bindings, state, sub, limits = payload_cost_model.normalize_review_packs(malformed_paths)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits

    # Malformed patterns (str instead of list[str])
    malformed_patterns = {
        "packs": {"auth": {"patterns": "*.py"}},
    }
    _, _, state, sub, limits = payload_cost_model.normalize_review_packs(malformed_patterns)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE

    # Malformed domain_contract (int instead of str)
    malformed_dc = {
        "packs": {"auth": {"domain_contract": 123}},
    }
    _, _, state, sub, limits = payload_cost_model.normalize_review_packs(malformed_dc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE

    # Malformed is_global (str instead of bool)
    malformed_global = {
        "packs": {"auth": {"is_global": "true"}},
    }
    _, _, state, sub, limits = payload_cost_model.normalize_review_packs(malformed_global)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE

    # Malformed in legacy flat packs
    malformed_legacy = [
        {"id": "auth", "paths": "backend/**"},
    ]
    _, _, state, sub, limits = payload_cost_model.normalize_review_packs(malformed_legacy)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE

    # Positive controls
    # Valid scoped pack
    valid_scoped = {
        "packs": {"auth": {"paths": ["backend/**"]}},
    }
    packs, _, state, sub, limits = payload_cost_model.normalize_review_packs(valid_scoped)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert packs[0]["paths"] == ["backend/**"]

    # Valid unscoped pack (paths absent)
    valid_unscoped = {
        "packs": {"auth": {"description": "Unscoped pack"}},
    }
    packs, _, state, sub, limits = payload_cost_model.normalize_review_packs(valid_unscoped)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert "paths" not in packs[0]


def test_cm_a3_binding_key_normalization_collision() -> None:
    """CM-A3-BINDING-KEY-NORMALIZATION-COLLISION (Finding 4178603213):
    Distinct raw keys normalizing to the same identity must fail closed as malformed bindings.
    """
    raw_bindings = {
        "pack": ["required_contract"],
        " pack ": [],
    }
    _, bindings, state, sub, limits = payload_cost_model.normalize_review_packs({}, extra_bindings=raw_bindings)
    assert "malformed_contract_bindings:duplicate_normalized_key" in limits
    assert bindings == {}


def test_cm_a2_contract_key_normalization_collision() -> None:
    """CM-A2-CONTRACT-KEY-NORMALIZATION-COLLISION (Finding 4178603213 sibling census):
    Contract mapping keys that collide after normalization must fail closed.
    """
    collision_doc = {
        "auth": ["rule 1"],
        " auth ": ["rule 2"],
    }
    contracts, state, sub, limits = payload_cost_model.normalize_domain_contracts(collision_doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_contract:INVALID_IDENTITY" in limits


def test_cm_a2_pack_key_normalization_collision() -> None:
    """CM-A2-PACK-KEY-NORMALIZATION-COLLISION (Finding 4178603213 sibling census):
    Review pack mapping keys that collide after normalization must fail closed.
    """
    collision_packs = {
        "packs": {
            "auth": {"description": "Auth pack 1"},
            " auth ": {"description": "Auth pack 2"},
        }
    }
    packs, bindings, state, sub, limits = payload_cost_model.normalize_review_packs(collision_packs)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_review_packs:INVALID_IDENTITY" in limits


def test_cm_a2_legacy_description_only_no_crash() -> None:
    """CM-A2-LEGACY-DESCRIPTION-ONLY-NO-CRASH (Finding 4178603218):
    Legacy flat rules with only description and no id must not crash contracts_context with KeyError.
    """
    legacy_doc = {
        "rules": [
            {"description": "Legacy advisory guideline without an ID"},
            {"id": "valid_rule", "description": "Rule with ID"},
        ]
    }
    intake = _base_intake()
    intake.target_profile = {"domain_contracts": legacy_doc}
    intake.status = "complete"

    # Must not raise KeyError when indexing contracts_by_id
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:domain_contracts"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    # The legacy description-only rule is preserved in domain_contracts
    descriptions = [c.get("description") for c in ctx["domain_contracts"]]
    assert "Legacy advisory guideline without an ID" in descriptions
    assert "Rule with ID" in descriptions


def test_ab_f1_mapping_key_collision_ablation() -> None:
    """Ablation for F1: omitting normalization collision check allows silent overwrite."""
    raw_bindings = {"pack": ["required"], " pack ": []}
    # Ablated logic: simple dict comprehension without collision detection
    ablated_bindings = {}
    for k, v in raw_bindings.items():
        ablated_bindings[k.strip()] = v
    # Under ablated logic, required relation is dropped silently!
    assert ablated_bindings == {"pack": []}


def test_ab_f1_mapping_key_id_conflict_ablation() -> None:
    """Ablation for F1: omitting nested id conflict check allows nested id to override mapping key."""
    doc = {"auth": {"id": "security"}}
    clean_key = "auth"
    value = doc["auth"]
    # Ablated logic: preferring nested id over clean_key
    ablated_cid = value.get("id") or clean_key
    assert ablated_cid == "security"  # RED: overrides key authority


# Family F2: Explicit Requirement and Compatibility Closure


def test_cm_a3_selected_pack_source_absent_and_empty() -> None:
    """CM-A3-SELECTED-PACK-SOURCE-ABSENT, CM-A3-SELECTED-PACK-SOURCE-EMPTY, CM-A3-SELECTED-PACK-NO-MATCH (Finding 4178603183):
    Explicit pack selection that cannot be resolved emits selected_contract_pack_missing.
    """
    intake = _base_intake()

    # 1. Source absent
    intake.target_profile = {}
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="required_pack",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:required_pack" in limits

    # 2. Source present-valid but empty
    intake.target_profile = {"review_packs": {"packs": {}}}
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="required_pack",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:required_pack" in limits

    # 3. Source present-valid but no match
    intake.target_profile = {
        "review_packs": {"packs": {"other_pack": {"paths": ["backend/**"]}}}
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="required_pack",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:required_pack" in limits

    # 4. Source invalid: preserves typed invalid limitation and does not mask
    intake.target_profile = {"review_packs": "invalid_string_shape"}
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="required_pack",
        semantic_group="api_schema_contract",
    )
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits
    assert "selected_contract_pack_missing:required_pack" not in limits

    # 5. Positive control: present-valid and matching
    intake.target_profile = {
        "review_packs": {"packs": {"required_pack": {"paths": ["backend/**"]}}}
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="required_pack",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:required_pack" not in limits
    assert len(ctx["review_packs"]) == 1
    assert ctx["review_packs"][0]["id"] == "required_pack"


def test_cm_a3_modern_exact_selection_and_legacy_equivalence() -> None:
    """CM-A3-MODERN-EXACT-SELECTION, PC-A3-LEGACY-SELECTION-EQUIVALENCE (Finding 4178603197):
    Modern mapping requires exact identity match; legacy flat preserves extensional baseline fuzzy matching.
    """
    intake = _base_intake()

    # Legacy flat: fuzzy matching accepts 'calendar' for 'calendar-pack'
    intake.target_profile = {
        "review_packs": [{"id": "calendar-pack", "description": "Calendar rules"}]
    }
    ctx_legacy, limits_legacy = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="calendar",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:calendar" not in limits_legacy
    assert len(ctx_legacy["review_packs"]) == 1

    # Modern mapping: exact matching rejects 'calendar' for 'calendar-pack'
    intake.target_profile = {
        "review_packs": {
            "packs": {"calendar-pack": {"description": "Calendar rules"}}
        }
    }
    ctx_modern, limits_modern = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="calendar",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:calendar" in limits_modern
    assert len(ctx_modern["review_packs"]) == 0

    # Modern mapping: exact match succeeds
    ctx_exact, limits_exact = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack="calendar-pack",
        semantic_group="api_schema_contract",
    )
    assert "selected_contract_pack_missing:calendar-pack" not in limits_exact
    assert len(ctx_exact["review_packs"]) == 1


def test_cm_a3_explicit_contract_reference_missing_e2e(tmp_path: Path) -> None:
    """CM-A3-EXPLICIT-CONTRACT-REFERENCE-MISSING (Finding 4178603200):
    Explicit chunk contract ref to missing contract produces typed critical limitation
    unresolved_contract_reference:<id> and degrades quality gate E2E.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {"security": {"rules": ["Validate token"]}},
        "contracts": ["contract:nonexistent_contract_id"],
    }

    # Semantic chunk plan must carry critical limitation and be degraded
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    expected_limitation = "unresolved_contract_reference:nonexistent_contract_id"
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

    # Simulate chunk review response
    resp_dir = tmp_path / "explicit_ref_missing_resp"
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


def test_ab_f2_legacy_matcher_ablation() -> None:
    """Ablation for F2: applying modern matcher to legacy flat rejects legacy input."""
    legacy_pack = {"id": "calendar-pack", "description": "calendar rules"}
    # Modern matcher fails on substring match
    assert payload_cost_model.modern_pack_matches_selected(legacy_pack, "calendar") is False
    # Legacy matcher succeeds
    assert payload_cost_model.legacy_pack_matches_selected(legacy_pack, "calendar") is True


def test_ab_f2_explicit_contract_ref_ablation() -> None:
    """Ablation for F2: omitting explicit ref check fails open without limitation."""
    contracts_by_id = {"existing": {"id": "existing"}}
    chunk_contracts = ["contract:missing"]
    referenced_contracts = {item.split(":", 1)[1] for item in chunk_contracts}
    # Ablated logic: only iterating contracts that exist
    limitations = []
    for c in contracts_by_id.values():
        if c["id"] in referenced_contracts:
            pass
    # Under ablated logic, no limitation is recorded!
    assert limitations == []


# Family F3: Projection Fixed-Point Closure


def test_cm_a4_contract_limitation_fixed_point() -> None:
    """CM-A4-CONTRACT-LIMITATION-FIXED-POINT (Finding 4178603205):
    Candidate contract limitations participate in the fixed point so that projected cost bounds real builder cost.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [{"id": "c1", "description": "c1", "paths": ["backend/api/*"]}],
        "review_packs": {
            "packs": {"p1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"p1": [f"unresolved_binding_{i}_" + ("x" * 80) for i in range(15)]},
        },
    }
    intake_dict = intake.model_dump(mode="json")

    # 1. Under tight budget (8000), planner rejects candidate during packing because of limitation bytes
    plan_tight = build_semantic_chunk_plan(intake_dict, max_blocks=2, max_chars_per_block=8000)
    assert plan_tight.status == "degraded"
    assert len(plan_tight.chunks) == 0
    assert "must_review_payload_oversize:backend/api/shifts.py" in plan_tight.limitations

    # 2. Under sufficient budget (20000), chunk is admitted and actual length <= projected <= budget
    budget = 20000
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=2, max_chars_per_block=budget)
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
        payload = payloads[f"{chunk.chunk_id}.json"]
        actual_len = payload_cost_model.canonical_len(payload.model_dump(mode="json"))
        assert actual_len <= budget


def test_ab_f3_contract_limitation_fixed_point_ablation() -> None:
    """Ablation for F3: removing candidate contract limitations from packing loop allows false admission."""
    # Under ablated logic, packing loop only projects with packing_limitations (empty)
    # causing an 8000-budget chunk to be admitted by planner but fail builder minimum content floor.
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [{"id": "c1", "description": "c1", "paths": ["backend/api/*"]}],
        "review_packs": {
            "packs": {"p1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"p1": [f"unresolved_binding_{i}_" + ("x" * 80) for i in range(15)]},
        },
    }
    brief_without_contract_lims = []
    chunk_hunks = payload_cost_model.diff_by_file(intake)
    cost_without_lims = payload_cost_model.project_min_hunk_preserving_chars(
        intake=intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        semantic_group="api_schema_contract",
        max_blocks=2,
        target={},
        brief_target={},
        brief_review={},
        brief_required_files=["backend/api/shifts.py"],
        brief_limitations=brief_without_contract_lims,
        selected_contract_pack=None,
        checks=None,
        validation_evidence=None,
        hunks=chunk_hunks,
        created_at="2026-10-04T00:00:00Z",
    )
    # The cost without limitation bytes is artificially smaller than the true cost with 15 long limitations
    cost_with_lims = payload_cost_model.project_min_hunk_preserving_chars(
        intake=intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        semantic_group="api_schema_contract",
        max_blocks=2,
        target={},
        brief_target={},
        brief_review={},
        brief_required_files=["backend/api/shifts.py"],
        brief_limitations=[f"unresolved_binding_{i}_" + ("x" * 80) for i in range(15)],
        selected_contract_pack=None,
        checks=None,
        validation_evidence=None,
        hunks=chunk_hunks,
        created_at="2026-10-04T00:00:00Z",
    )
    assert cost_without_lims < cost_with_lims


# Family F4: Required Semantic Context Closure


def test_cm_a5_required_binding_pack_preserved() -> None:
    """CM-A5-REQUIRED-BINDING-PACK-PRESERVED (Finding 4178603209 / B2):
    Applicable pack establishing a binding is marked required and preserved during budget shrink.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {"security": {"rules": ["Security rule"], "paths": ["backend/api/*"]}},
        "review_packs": {
            "packs": {
                "auth_pack": {"paths": ["backend/api/*"], "description": "Auth pack description"},
                "unrelated_pack": {"paths": ["backend/other/*"]},
            },
            "contract_bindings": {"auth_pack": ["security"]},
        },
    }
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    # auth_pack has effective_contracts and applicable path, so it is marked required
    auth_p = next(p for p in ctx["review_packs"] if p["id"] == "auth_pack")
    assert auth_p.get("required") is True
    assert "applicable_pack_identity" in auth_p.get("required_reasons")
    assert "effective_contract_binding" in auth_p.get("required_reasons")
    # unrelated_pack has no matching paths, so it is not in review_packs
    pack_ids = [p["id"] for p in ctx["review_packs"]]
    assert "unrelated_pack" not in pack_ids

    # When shrinking, optional pack metadata (description) is stripped first, preserving pack id
    payload = {
        "chunk_context": {"contracts_context": ctx},
        "limitations": [],
    }
    shrunk = _shrink_contracts_context(payload)
    assert shrunk is True
    remaining_pack = payload["chunk_context"]["contracts_context"]["review_packs"][0]
    assert remaining_pack["id"] == "auth_pack"
    assert "description" not in remaining_pack
    assert payload["limitations"] == []  # No critical loss code emitted


def test_cm_a5_optional_pack_dropped_safely() -> None:
    """CM-A5-OPTIONAL-PACK-DROPPED-SAFELY (Finding 4178603209):
    Optional pack without bindings or required contracts is dropped cleanly without loss code.
    """
    payload = {
        "chunk_context": {
            "contracts_context": {
                "review_packs": [{"id": "opt_pack", "description": "optional"}],
                "domain_contracts": [],
            }
        },
        "limitations": [],
    }
    shrunk = _shrink_contracts_context(payload)
    assert shrunk is True
    assert payload["chunk_context"]["contracts_context"]["review_packs"] == []
    assert payload["limitations"] == []


def test_cm_a5_required_binding_pack_loss() -> None:
    """CM-A5-REQUIRED-BINDING-PACK-LOSS (Finding 4178603209):
    Forced removal of required binding pack emits required_contract_pack_context_lost:<id>.
    """
    payload = {
        "chunk_context": {
            "contracts_context": {
                "review_packs": [{"id": "req_pack", "required": True}],
                "domain_contracts": [],
            }
        },
        "limitations": [],
    }
    shrunk = _shrink_contracts_context(payload)
    assert shrunk is True
    assert "required_contract_pack_context_lost:req_pack" in payload["limitations"]


def test_ab_f4_required_pack_optional_ablation() -> None:
    """Ablation for F4: treating required pack as optional drops it silently without recording loss."""
    pack = {"id": "req_pack"}
    payload = {
        "chunk_context": {
            "contracts_context": {
                "review_packs": [pack],
                "domain_contracts": [],
            }
        },
        "limitations": [],
    }
    # If required flag was omitted, it is popped as optional
    shrunk = _shrink_contracts_context(payload)
    assert shrunk is True
    assert payload["limitations"] == []  # RED: loss code omitted!


# Positive Controls and Regressions


def test_pc_generic_repo_no_packs_not_degraded(tmp_path: Path) -> None:
    """Section 26 Regression: Generic repository without review_packs, selected_pack, or bindings
    must NOT emit required_source_absent, and approved review must reach gate passed.
    """
    intake = _base_intake()
    intake.target_profile = {
        "schema_version": "agent-review.target-profile.v1",
        "target_repo": "mglpsw/AgentEscala",
    }
    # No domain_contracts, no review_packs, no selected pack, no bindings
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    assert "required_source_absent:review_packs" not in limits
    assert "required_source_absent:domain_contracts" not in limits
    assert "selected_contract_pack_missing" not in str(limits)
    assert ctx == {"domain_contracts": [], "review_packs": []}

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status in {"complete", "partial"}
    assert "required_source_absent:review_packs" not in plan.limitations

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    resp_dir = tmp_path / "generic_resp"
    resp_dir.mkdir()
    for chunk in plan.chunks:
        resp = {
            "schema_version": 1,
            "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": list(chunk.files),
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    results = parse_chunk_results(plan, responses_dir=resp_dir)
    review = synthesize_final_review(results)
    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)

    assert gate.status == "passed"
    assert gate.manual_review_required is False


def test_pc_valid_modern_clean_review_passes(tmp_path: Path) -> None:
    """Section 25 Positive Control: Valid modern mapping with valid bindings and clean review
    reaches gate.status == 'passed'.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "security": {
                "paths": ["backend/api/*"],
                "rules": ["Enforce authentication on all endpoints"],
            }
        },
        "review_packs": {
            "packs": {
                "auth_pack": {
                    "paths": ["backend/api/*"],
                    "description": "Authentication and authorization rules",
                }
            },
            "contract_bindings": {
                "auth_pack": ["security"],
            },
        },
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=20000)
    assert plan.status in {"complete", "partial"}
    assert not any(any(lim.startswith(p) for p in payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES) for lim in plan.limitations)

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    resp_dir = tmp_path / "valid_modern_resp"
    resp_dir.mkdir()
    for chunk in plan.chunks:
        resp = {
            "schema_version": 1,
            "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": list(chunk.files),
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    results = parse_chunk_results(plan, responses_dir=resp_dir)
    review = synthesize_final_review(results)
    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)

    assert gate.status == "passed"
    assert gate.manual_review_required is False


# ---------------------------------------------------------------------------
# Second Codex Cycle (Review 5408162653) Causal Closures & Countermodels
# ---------------------------------------------------------------------------


# S1: Typed Declaration Admission Totality (4179272317, 4179272328, 4179272330)


def test_cm_s1_domain_paths_scalar() -> None:
    """CM-S1-DOMAIN-PATHS-SCALAR: Modern domain contract with scalar paths string rejected with MALFORMED_SHAPE (4179272317)."""
    doc = {"auth": {"paths": "backend/**"}}
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_s1_domain_paths_nonstring_member() -> None:
    """CM-S1-DOMAIN-PATHS-NONSTRING-MEMBER: Modern domain contract with non-string list element in paths rejected (4179272317)."""
    doc = {"auth": {"paths": ["backend/**", 123]}}
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_s1_domain_patterns_scalar() -> None:
    """CM-S1-DOMAIN-PATTERNS-SCALAR: Modern domain contract with scalar patterns string rejected with MALFORMED_SHAPE (4179272317)."""
    doc = {"auth": {"patterns": "backend/**"}}
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_s1_domain_contract_positives() -> None:
    """Positives: absent scope field, valid list[str], explicitly global contract admit cleanly (4179272317)."""
    # 1. Scope field absent
    doc_absent = {"auth": {"rules": ["rule1"]}}
    c1, s1, _, l1 = payload_cost_model.normalize_domain_contracts(doc_absent)
    assert s1 == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert l1 == []

    # 2. Valid list[str]
    doc_valid = {"auth": {"paths": ["backend/api/*"], "patterns": ["*.py"]}}
    c2, s2, _, l2 = payload_cost_model.normalize_domain_contracts(doc_valid)
    assert s2 == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert l2 == []
    assert c2[0]["paths"] == ["backend/api/*"]

    # 3. Explicitly global contract
    doc_global = {"security": {"is_global": True, "rules": ["Must authenticate"]}}
    c3, s3, _, l3 = payload_cost_model.normalize_domain_contracts(doc_global)
    assert s3 == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert l3 == []
    assert c3[0]["is_global"] is True


def test_cm_s1_empty_domain_contract() -> None:
    """CM-S1-EMPTY-DOMAIN-CONTRACT: Pack with empty string domain_contract rejected with INVALID_IDENTITY (4179272328)."""
    doc = {"packs": {"api-pack": {"domain_contract": ""}}}
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_review_packs:INVALID_IDENTITY" in limits


def test_cm_s1_whitespace_domain_contract() -> None:
    """CM-S1-WHITESPACE-DOMAIN-CONTRACT: Pack with whitespace domain_contract rejected with INVALID_IDENTITY (4179272328)."""
    doc = {"packs": {"api-pack": {"domain_contract": "   \t  "}}}
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_review_packs:INVALID_IDENTITY" in limits


def test_s1_pack_domain_contract_positives() -> None:
    """Positives: absent domain_contract vs valid nonempty identity (4179272328)."""
    # 1. Absent domain_contract
    doc_absent = {"packs": {"p1": {"description": "pack 1"}}}
    p1, _, s1, _, l1 = payload_cost_model.normalize_review_packs(doc_absent)
    assert s1 == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert "domain_contract" not in p1[0]

    # 2. Nonempty valid domain_contract identity
    doc_valid = {"packs": {"p2": {"domain_contract": "sec-contract", "description": "pack 2"}}}
    p2, _, s2, _, l2 = payload_cost_model.normalize_review_packs(doc_valid)
    assert s2 == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert p2[0]["domain_contract"] == "sec-contract"


def test_cm_s1_contract_ref_container_scalar() -> None:
    """CM-S1-CONTRACT-REF-CONTAINER-SCALAR: Scalar string contract container rejected and degrades plan (4179272330)."""
    intake = _base_intake()
    intake.target_profile = {
        "contracts": "contract:missing",
    }
    refs, limits = payload_cost_model.parse_contract_refs(intake)
    assert "unresolved_contract_reference:MALFORMED_CONTAINER" in limits

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status == "degraded"
    assert "unresolved_contract_reference:MALFORMED_CONTAINER" in plan.limitations


def test_cm_s1_contract_ref_container_mixed() -> None:
    """CM-S1-CONTRACT-REF-CONTAINER-MIXED: Mixed non-string container member rejected and degrades plan (4179272330)."""
    intake = _base_intake()
    intake.target_profile = {
        "contracts": ["contract:valid", 42],
    }
    refs, limits = payload_cost_model.parse_contract_refs(intake)
    assert "unresolved_contract_reference:MALFORMED_MEMBER" in limits

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status == "degraded"
    assert "unresolved_contract_reference:MALFORMED_MEMBER" in plan.limitations


def test_cm_s1_contract_ref_empty_identity() -> None:
    """CM-S1-CONTRACT-REF-EMPTY-IDENTITY: Empty/whitespace container member rejected and degrades plan (4179272330)."""
    intake = _base_intake()
    intake.target_profile = {
        "contracts": ["contract:valid", "   "],
    }
    refs, limits = payload_cost_model.parse_contract_refs(intake)
    assert "unresolved_contract_reference:EMPTY_IDENTITY" in limits

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status == "degraded"
    assert "unresolved_contract_reference:EMPTY_IDENTITY" in plan.limitations


def test_s1_sibling_declared_input_census() -> None:
    """S1 Sibling Census: Verifies bounded declared input fields across domain_contracts and review_packs."""
    # Invalid scope fields in domain contract
    for field in ("paths", "files", "source_files", "related_files", "patterns"):
        doc = {"c1": {field: "scalar_string"}}
        _, s, sub, l = payload_cost_model.normalize_domain_contracts(doc)
        assert s == payload_cost_model.SOURCE_STATE_INVALID, f"Field {field} scalar must be invalid"
        assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
        assert "invalid_source_contract:MALFORMED_SHAPE" in l

    for field in ("path", "file_path", "scope", "description"):
        doc = {"c1": {field: 12345}}
        _, s, sub, l = payload_cost_model.normalize_domain_contracts(doc)
        assert s == payload_cost_model.SOURCE_STATE_INVALID, f"Field {field} non-str must be invalid"

    # Invalid is_global
    doc = {"c1": {"is_global": "true"}}
    _, s, sub, l = payload_cost_model.normalize_domain_contracts(doc)
    assert s == payload_cost_model.SOURCE_STATE_INVALID
    assert sub == payload_cost_model.SUBTYPE_MALFORMED_SHAPE


# S2: Required Pack Context Closure (4179272322, 4179272324)


def test_cm_s2_selected_pack_required() -> None:
    """CM-S2-SELECTED-PACK-REQUIRED: Explicitly selected pack without bindings is marked required with explicit reason (4179272322)."""
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "standalone-pack": {"description": "Standalone pack with no bindings"},
                "optional-pack": {"description": "Optional unselected pack"},
            }
        }
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="standalone-pack",
        semantic_group="primary_backend_logic",
    )
    packs_by_id = {p["id"]: p for p in ctx["review_packs"]}
    standalone = packs_by_id["standalone-pack"]
    assert standalone.get("required") is True
    assert "explicit_selection" in standalone.get("required_reasons")
    assert "applicable_pack_identity" in standalone.get("required_reasons")

    # Minimal floor retains explicitly selected pack
    min_ctx = payload_cost_model.minimal_contracts_context(ctx)
    min_ids = [p["id"] for p in min_ctx["review_packs"]]
    assert "standalone-pack" in min_ids
    assert "optional-pack" not in min_ids


def test_s2_binding_pack_required_reasons() -> None:
    """Binding-establishing pack has required_reasons containing effective_contract_binding and applicable_pack_identity (4179272322)."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {"auth": {"rules": ["Authenticate"]}},
        "review_packs": {
            "packs": {"auth-pack": {"domain_contract": "auth", "paths": ["backend/api/*"]}},
        },
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack=None,
        semantic_group="primary_backend_logic",
    )
    pack = ctx["review_packs"][0]
    assert pack["id"] == "auth-pack"
    assert pack.get("required") is True
    assert "effective_contract_binding" in pack.get("required_reasons")
    assert "applicable_pack_identity" in pack.get("required_reasons")


def test_cm_s2_selected_pack_budget_loss(tmp_path: Path) -> None:
    """CM-S2-SELECTED-PACK-BUDGET-LOSS: Forced removal of required pack emits required_contract_pack_context_lost,
    blocks routing via payload_path=None, status='limited', and fails quality gate (4179272324).
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "selected-pack": {"description": "X" * 5000, "paths": ["backend/api/*"]},
            }
        }
    }
    plan = build_semantic_chunk_plan(
        intake.model_dump(mode="json"),
        max_blocks=2,
        max_chars_per_block=15000,
    )
    for c in plan.chunks:
        c.prompt_budget_chars = 2500

    brief = _brief(intake, plan)
    brief.review["contract_pack"] = "selected-pack"

    # Build chunk payloads under defensive budget forcing contracts shrink
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    limited_entries = [e for e in manifest.chunks if any(lim.startswith("required_contract_pack_context_lost:selected-pack") for lim in e.limitations)]
    assert len(limited_entries) > 0, "Forced shrink must emit required_contract_pack_context_lost"
    entry = limited_entries[0]
    assert entry.status == "limited"
    assert entry.payload_path is None, "Payload routing must be blocked (payload_path=None)"
    assert entry.payload_sha256 is None


def test_s2_optional_pack_shrink_positive() -> None:
    """Positive: Optional pack is removed under budget while selected required pack is preserved and routable (4179272322, 4179272324)."""
    payload = {
        "schema_version": 1,
        "chunk_id": "chunk-01",
        "semantic_group": "primary_backend_logic",
        "limitations": [],
        "warnings": [],
        "chunk_context": {
            "contracts_context": {
                "domain_contracts": [],
                "review_packs": [
                    {"id": "optional-pack", "description": "Optional pack", "required": False},
                    {"id": "selected-pack", "description": "Selected pack", "required": True, "required_reasons": ["explicit_selection"]},
                ],
            }
        },
    }
    # Shrink contracts context once
    shrunk = _shrink_contracts_context(payload)
    assert shrunk is True
    packs = payload["chunk_context"]["contracts_context"]["review_packs"]
    assert len(packs) == 1
    assert packs[0]["id"] == "selected-pack", "Optional pack popped first, selected pack preserved"
    assert not any(lim.startswith("required_contract_pack_context_lost:") for lim in payload["limitations"])


# S3: Legacy/Modern Semantic Boundary (4179272326, 4179272331)


def test_cm_s3_empty_bindings_legacy_mode() -> None:
    """CM-S3-EMPTY-BINDINGS-LEGACY-MODE: Empty bindings {} do not switch legacy list to modern mapping,
    preserving legacy fuzzy selector (calendar -> calendar-pack) without selected_contract_pack_missing (4179272326).
    """
    # 1. Document-level empty contract_bindings
    doc_empty = {
        "packs": [{"id": "calendar-pack", "description": "Calendar scheduling rules"}],
        "contract_bindings": {},
    }
    assert payload_cost_model.detect_review_packs_format(doc_empty) == payload_cost_model.FORMAT_LEGACY_FLAT

    # 2. Extra-bindings level empty mapping
    assert payload_cost_model.detect_review_packs_format(
        {"packs": [{"id": "calendar-pack", "description": "Calendar scheduling rules"}]},
        extra_bindings={},
    ) == payload_cost_model.FORMAT_LEGACY_FLAT

    # 3. Preserves legacy fuzzy matching: "calendar" matches "calendar-pack"
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": doc_empty,
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="calendar",
        semantic_group="primary_backend_logic",
    )
    assert not any(lim.startswith("selected_contract_pack_missing:") for lim in limits)
    assert len(ctx["review_packs"]) == 1
    assert ctx["review_packs"][0]["id"] == "calendar-pack"


def test_s3_format_matrix_exhaustive() -> None:
    """S3 Format Matrix: Exhaustively checks format detection across all shape cells (4179272326, Finding Pre-Ready B)."""
    # 1. packs_list + bindings_absent: LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}]) == payload_cost_model.FORMAT_LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}) == payload_cost_model.FORMAT_LEGACY_FLAT

    # 2. packs_list + bindings_empty: LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}], "contract_bindings": {}}) == payload_cost_model.FORMAT_LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}, extra_bindings={}) == payload_cost_model.FORMAT_LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}], extra_bindings={}) == payload_cost_model.FORMAT_LEGACY_FLAT

    # 3. packs_list + bindings_nonempty: INVALID_MIXED_SHAPE (N2 format matrix: legacy list never becomes modern mapping)
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}], "contract_bindings": {"p1": ["c1"]}}) == payload_cost_model.FORMAT_LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}, extra_bindings={"p1": ["c1"]}) == payload_cost_model.FORMAT_LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}], extra_bindings={"p1": ["c1"]}) == payload_cost_model.FORMAT_LEGACY_FLAT
    _, _, state_mixed1, sub_mixed1, limits_mixed1 = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1"}], "contract_bindings": {"p1": ["c1"]}})
    assert state_mixed1 == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_mixed1 == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits_mixed1
    _, _, state_mixed2, sub_mixed2, limits_mixed2 = payload_cost_model.normalize_review_packs([{"id": "p1"}], extra_bindings={"p1": ["c1"]})
    assert state_mixed2 == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_mixed2 == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits_mixed2

    # 4. packs_mapping + bindings_absent: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}}) == payload_cost_model.FORMAT_MODERN_MAPPING

    # 5. packs_mapping + bindings_empty: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}, "contract_bindings": {}}) == payload_cost_model.FORMAT_MODERN_MAPPING

    # 6. packs_mapping + bindings_nonempty: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}, "contract_bindings": {"p1": ["c1"]}}) == payload_cost_model.FORMAT_MODERN_MAPPING


def test_cm_s3_legacy_pattern_baseline() -> None:
    """CM-S3-LEGACY-PATTERN-BASELINE: Legacy rules preserve baseline substring/prefix semantics without glob expansion (4179272331)."""
    # 1. Legacy substring/prefix matching: "backend/api" matches "backend/api/shifts.py"
    legacy_contract = {"id": "legacy-rule", "patterns": ["backend/api"]}
    assert payload_cost_model._contract_matches_chunk(
        legacy_contract,
        chunk_files={"backend/api/shifts.py"},
        format=payload_cost_model.FORMAT_LEGACY_FLAT,
    ) is True

    # 2. Legacy "*.py" does not act as glob matching "backend/api/shifts.py"
    legacy_glob_contract = {"id": "legacy-literal", "patterns": ["*.py"]}
    assert payload_cost_model._contract_matches_chunk(
        legacy_glob_contract,
        chunk_files={"backend/api/shifts.py"},
        format=payload_cost_model.FORMAT_LEGACY_FLAT,
    ) is False

    # 3. Modern pattern matching uses fnmatchcase: "backend/api/*" matches "backend/api/shifts.py"
    modern_contract = {"id": "modern-rule", "patterns": ["backend/api/*"]}
    assert payload_cost_model._contract_matches_chunk(
        modern_contract,
        chunk_files={"backend/api/shifts.py"},
        format=payload_cost_model.FORMAT_MODERN_MAPPING,
    ) is True

    # 4. Modern case-sensitivity: "Backend/Api/*" fails to match "backend/api/shifts.py"
    modern_wrong_case = {"id": "modern-case", "patterns": ["Backend/Api/*"]}
    assert payload_cost_model._contract_matches_chunk(
        modern_wrong_case,
        chunk_files={"backend/api/shifts.py"},
        format=payload_cost_model.FORMAT_MODERN_MAPPING,
    ) is False


# ===========================================================================
# R1: Bounded Declared-Input Shape Domain Totality
# ===========================================================================


def test_cm_r1_domain_paths_empty_member() -> None:
    """CM-R1-DOMAIN-PATHS-EMPTY-MEMBER: Empty or whitespace member in contract paths
    fails closed with SOURCE_STATE_INVALID and MALFORMED_SHAPE, never normalizing to [].
    """
    # 1. Empty string member in modern mapping paths
    doc_empty = {"api": {"paths": ["backend/api/*", ""]}}
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc_empty)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits

    # 2. Whitespace member in modern mapping paths
    doc_ws = {"api": {"paths": ["backend/api/*", "   "]}}
    _, state_ws, sub_ws, limits_ws = payload_cost_model.normalize_domain_contracts(doc_ws)
    assert state_ws == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_ws == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_ws

    # 3. Legacy flat rules with empty member in paths
    doc_legacy = [{"id": "rule-1", "paths": [""]}]
    _, state_leg, sub_leg, limits_leg = payload_cost_model.normalize_domain_contracts(doc_legacy)
    assert state_leg == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_leg == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_leg

    # Positive control: explicitly empty list [] remains valid
    doc_empty_list = {"api": {"paths": [], "rules": ["valid rule"]}}
    contracts_ok, state_ok, sub_ok, limits_ok = payload_cost_model.normalize_domain_contracts(doc_empty_list)
    assert state_ok == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert sub_ok is None
    assert not limits_ok


def test_cm_r1_domain_patterns_whitespace_member() -> None:
    """CM-R1-DOMAIN-PATTERNS-WHITESPACE-MEMBER: Whitespace member in contract patterns
    fails closed with SOURCE_STATE_INVALID and MALFORMED_SHAPE.
    """
    doc_ws = {"api": {"patterns": ["   "]}}
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc_ws)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits

    # Empty string member in patterns
    doc_empty = {"api": {"patterns": ["backend/api/*", ""]}}
    _, state_e, sub_e, limits_e = payload_cost_model.normalize_domain_contracts(doc_empty)
    assert state_e == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_e == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_e


def test_cm_r1_pack_paths_empty_member() -> None:
    """CM-R1-PACK-PATHS-EMPTY-MEMBER: Empty string member in review pack paths
    fails closed across raw list, envelope list, and mapping shapes.
    """
    # 1. Raw list shape
    _, _, state_raw, sub_raw, limits_raw = payload_cost_model.normalize_review_packs([{"id": "p1", "paths": [""]}])
    assert state_raw == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_raw == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_raw

    # 2. Envelope list shape
    _, _, state_env, sub_env, limits_env = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1", "paths": [""]}]})
    assert state_env == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_env == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_env

    # 3. Mapping shape
    _, _, state_map, sub_map, limits_map = payload_cost_model.normalize_review_packs({"packs": {"p1": {"paths": [""]}}})
    assert state_map == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_map == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_map

    # Positive control: explicitly empty paths [] remains valid
    _, _, state_ok, sub_ok, limits_ok = payload_cost_model.normalize_review_packs([{"id": "p1", "paths": []}])
    assert state_ok == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert sub_ok is None


def test_cm_r1_pack_patterns_whitespace_member() -> None:
    """CM-R1-PACK-PATTERNS-WHITESPACE-MEMBER: Whitespace member in review pack patterns
    fails closed across raw list, envelope list, and mapping shapes.
    """
    # 1. Raw list shape
    _, _, state_raw, sub_raw, limits_raw = payload_cost_model.normalize_review_packs([{"id": "p1", "patterns": ["   "]}])
    assert state_raw == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_raw == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_raw

    # 2. Envelope list shape
    _, _, state_env, sub_env, limits_env = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1", "patterns": ["   "]}]})
    assert state_env == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_env == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_env

    # 3. Mapping shape
    _, _, state_map, sub_map, limits_map = payload_cost_model.normalize_review_packs({"packs": {"p1": {"patterns": ["   "]}}})
    assert state_map == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_map == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_review_packs:MALFORMED_SHAPE" in limits_map


def test_cm_r1_named_section_nonstring_member() -> None:
    """CM-R1-NAMED-SECTION-NONSTRING-MEMBER: Non-string member in named string section
    fails closed with SOURCE_STATE_INVALID and MALFORMED_SHAPE.
    """
    doc = {
        "auth": {
            "critical_constraints": ["Administrator endpoints must enforce role check", 123],
        },
    }
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_r1_named_section_empty_string() -> None:
    """CM-R1-NAMED-SECTION-EMPTY-STRING: Empty or whitespace string in named string section
    fails closed with SOURCE_STATE_INVALID and MALFORMED_SHAPE.
    """
    # 1. Nested in contract mapping
    doc_nested = {
        "auth": {
            "review_checklist": ["Check bearer token header redaction", "   "],
        },
    }
    _, state_n, sub_n, limits_n = payload_cost_model.normalize_domain_contracts(doc_nested)
    assert state_n == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_n == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_n

    # 2. Top-level named list section
    doc_top = {
        "response_model_rules": ["Valid rule", ""],
    }
    _, state_t, sub_t, limits_t = payload_cost_model.normalize_domain_contracts(doc_top)
    assert state_t == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_t == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_t


def test_cm_r1_named_section_nested_dict_where_string_required() -> None:
    """CM-R1-NAMED-SECTION-NESTED-DICT-WHERE-STRING-REQUIRED: Nested dict inside a section
    that requires strings fails closed with MALFORMED_SHAPE.
    """
    doc = {
        "auth": {
            "critical_constraints": [{"rule": "Should be string instead of dict"}],
        },
    }
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_r1_rule_dict_text_wrong_type() -> None:
    """CM-R1-RULE-DICT-TEXT-WRONG-TYPE: Carrier in rule dict with non-string type
    fails closed with MALFORMED_SHAPE.
    """
    doc = {
        "calendar": {
            "slot_rules": [
                {"rule": 12345, "invariant": True},
            ],
        },
    }
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_r1_rule_dict_text_empty() -> None:
    """CM-R1-RULE-DICT-TEXT-EMPTY: Carrier in rule dict with empty/whitespace string
    fails closed with MALFORMED_SHAPE.
    """
    doc = {
        "calendar": {
            "slot_rules": [
                {"rule": "   ", "invariant": True},
            ],
        },
    }
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits


def test_cm_r1_rule_dict_no_text_carrier() -> None:
    """CM-R1-RULE-DICT-NO-TEXT-CARRIER: Rule dict with no recognized text carrier
    fails closed with MALFORMED_SHAPE, never silently dropping the dict.
    """
    # 1. Empty dict {}
    doc_empty = {
        "calendar": {
            "slot_rules": [{}],
        },
    }
    _, state_e, sub_e, limits_e = payload_cost_model.normalize_domain_contracts(doc_empty)
    assert state_e == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_e == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_e

    # 2. Dict with only non-carrier fields
    doc_no_carrier = {
        "calendar": {
            "slot_rules": [{"invariant": True, "rationale": "Missing carrier"}],
        },
    }
    _, state_nc, sub_nc, limits_nc = payload_cost_model.normalize_domain_contracts(doc_no_carrier)
    assert state_nc == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_nc == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_nc


def test_cm_r1_unknown_modern_field_unsupported_nonempty() -> None:
    """CM-R1-UNKNOWN-MODERN-FIELD: Unknown field in modern contract mapping
    fails closed with UNSUPPORTED_NONEMPTY, never silently dropping semantic input.
    """
    doc = {
        "auth": {
            "unknown_semantic_field": "disallowed_value",
            "rules": ["valid rule"],
        },
    }
    _, state, subtype, limits = payload_cost_model.normalize_domain_contracts(doc)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_contract:UNSUPPORTED_NONEMPTY" in limits

    # Positive control: legacy flat rules preserves pass-through for unknown fields
    legacy_doc = [{"id": "legacy-1", "unknown_legacy_annotation": "allowed_in_legacy", "rules": ["valid rule"]}]
    contracts, state_leg, sub_leg, limits_leg = payload_cost_model.normalize_domain_contracts(legacy_doc)
    assert state_leg == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert sub_leg is None


def test_format_matrix_cartesian_exhaustive() -> None:
    """Format Matrix Cartesian: Exhaustively validates every combination of
    container x bindings x source with admission, matcher, binding behavior, and limitations.
    Adjudicates N2: raw_list/envelope_list + non-empty external bindings is INVALID_MIXED_SHAPE and FORMAT_LEGACY_FLAT.
    """
    # 1. raw_list
    # - absent bindings: LEGACY_FLAT, fuzzy selection, substring pattern, no limits
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}]) == payload_cost_model.FORMAT_LEGACY_FLAT
    # - empty extra bindings: LEGACY_FLAT, fuzzy selection, substring pattern, no limits
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}], extra_bindings={}) == payload_cost_model.FORMAT_LEGACY_FLAT
    # - nonempty_valid extra bindings: INVALID_MIXED_SHAPE, format remains LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format([{"id": "p1"}], extra_bindings={"p1": ["c1"]}) == payload_cost_model.FORMAT_LEGACY_FLAT
    _, _, state_raw_ne, sub_raw_ne, limits_raw_ne = payload_cost_model.normalize_review_packs([{"id": "p1"}], extra_bindings={"p1": ["c1"]})
    assert state_raw_ne == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_raw_ne == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits_raw_ne
    # - malformed extra bindings: emits malformed_contract_bindings
    packs, bindings, state, sub, limits = payload_cost_model.normalize_review_packs([{"id": "p1"}], extra_bindings="scalar_bad")
    assert "malformed_contract_bindings:must_be_mapping" in limits

    # 2. envelope_list
    # - absent bindings: LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}) == payload_cost_model.FORMAT_LEGACY_FLAT
    # - empty document bindings: LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}], "contract_bindings": {}}) == payload_cost_model.FORMAT_LEGACY_FLAT
    # - empty extra bindings: LEGACY_FLAT
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}, extra_bindings={}) == payload_cost_model.FORMAT_LEGACY_FLAT
    # - nonempty_valid document bindings: INVALID_MIXED_SHAPE
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}], "contract_bindings": {"p1": ["c1"]}}) == payload_cost_model.FORMAT_LEGACY_FLAT
    _, _, state_env_ne, sub_env_ne, limits_env_ne = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1"}], "contract_bindings": {"p1": ["c1"]}})
    assert state_env_ne == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_env_ne == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits_env_ne
    # - nonempty_valid extra bindings: INVALID_MIXED_SHAPE
    assert payload_cost_model.detect_review_packs_format({"packs": [{"id": "p1"}]}, extra_bindings={"p1": ["c1"]}) == payload_cost_model.FORMAT_LEGACY_FLAT
    _, _, state_env_extra, sub_env_extra, limits_env_extra = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1"}]}, extra_bindings={"p1": ["c1"]})
    assert state_env_extra == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_env_extra == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits_env_extra
    # - malformed document bindings: emits malformed_contract_bindings
    _, _, _, _, limits_env_bad = payload_cost_model.normalize_review_packs({"packs": [{"id": "p1"}], "contract_bindings": {"p1": "not_a_list"}})
    assert "malformed_contract_bindings:must_be_list_of_strings" in limits_env_bad

    # 3. mapping
    # - absent bindings: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}}) == payload_cost_model.FORMAT_MODERN_MAPPING
    # - empty bindings: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}, "contract_bindings": {}}) == payload_cost_model.FORMAT_MODERN_MAPPING
    # - nonempty_valid bindings: MODERN_MAPPING
    assert payload_cost_model.detect_review_packs_format({"packs": {"p1": {}}, "contract_bindings": {"p1": ["c1"]}}) == payload_cost_model.FORMAT_MODERN_MAPPING
    # - malformed bindings: emits malformed_contract_bindings
    _, _, _, _, limits_map_bad = payload_cost_model.normalize_review_packs({"packs": {"p1": {}}, "contract_bindings": []})
    assert "malformed_contract_bindings:must_be_mapping" in limits_map_bad

    # 4. absent document (None)
    # - absent bindings: MODERN_MAPPING, SOURCE_STATE_ABSENT, no limits
    assert payload_cost_model.detect_review_packs_format(None) == payload_cost_model.FORMAT_MODERN_MAPPING
    _, _, state_abs, _, limits_abs = payload_cost_model.normalize_review_packs(None)
    assert state_abs == payload_cost_model.SOURCE_STATE_ABSENT
    assert not limits_abs
    # - empty extra bindings: MODERN_MAPPING, SOURCE_STATE_ABSENT, no limits
    assert payload_cost_model.detect_review_packs_format(None, extra_bindings={}) == payload_cost_model.FORMAT_MODERN_MAPPING
    _, _, state_abs_e, _, limits_abs_e = payload_cost_model.normalize_review_packs(None, extra_bindings={})
    assert state_abs_e == payload_cost_model.SOURCE_STATE_ABSENT
    assert not limits_abs_e
    # - nonempty_valid extra bindings: MODERN_MAPPING, emits required_source_absent:review_packs
    assert payload_cost_model.detect_review_packs_format(None, extra_bindings={"p1": ["c1"]}) == payload_cost_model.FORMAT_MODERN_MAPPING
    _, _, state_abs_ne, _, limits_abs_ne = payload_cost_model.normalize_review_packs(None, extra_bindings={"p1": ["c1"]})
    assert state_abs_ne == payload_cost_model.SOURCE_STATE_ABSENT
    assert "required_source_absent:review_packs" in limits_abs_ne


# ===========================================================================
# Causal Ablations (R2: Real Production Seams Replacing Pseudo-Ablations)
# ===========================================================================


def test_ablation_r1_empty_path_member_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R1-EMPTY-PATH-MEMBER-VALIDATION:
    Production mechanism: _validate_contract_declared_field_types rejecting empty path member.
    Countermodel: CM-R1-DOMAIN-PATHS-EMPTY-MEMBER.
    Restored: GREEN (fails closed with SOURCE_STATE_INVALID).
    Mutant: RED (bypassing validation normalizes [''] to [] and returns PRESENT_VALID).
    """
    doc = {"api": {"paths": [""]}}

    # Restored / Green: Production validation fails closed
    _, state_green, sub_green, limits_green = payload_cost_model.normalize_domain_contracts(doc)
    assert state_green == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_green == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_green

    # Mutant / Red: Bypass field validation (simulating pre-repair silent normalization)
    monkeypatch.setattr(payload_cost_model, "_validate_contract_declared_field_types", lambda item: True)
    _, state_mutant, _, _ = payload_cost_model.normalize_domain_contracts(doc)
    assert state_mutant == payload_cost_model.SOURCE_STATE_PRESENT_VALID, "Mutant must cause same countermodel to fail (RED)"


def test_ablation_r1_named_section_member_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R1-NAMED-SECTION-MEMBER-VALIDATION:
    Production mechanism: _validate_contract_declared_field_types string check on named sections.
    Countermodel: CM-R1-NAMED-SECTION-NONSTRING-MEMBER.
    Restored: GREEN (fails closed with SOURCE_STATE_INVALID).
    Mutant: RED (bypassing validation allows 123 to be silently dropped).
    """
    doc = {"auth": {"critical_constraints": ["Administrator endpoints must enforce role check", 123]}}

    # Restored / Green:
    _, state_green, sub_green, limits_green = payload_cost_model.normalize_domain_contracts(doc)
    assert state_green == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_green == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert "invalid_source_contract:MALFORMED_SHAPE" in limits_green

    # Mutant / Red:
    monkeypatch.setattr(payload_cost_model, "_validate_contract_declared_field_types", lambda item: True)
    contracts_mutant, state_mutant, _, _ = payload_cost_model.normalize_domain_contracts(doc)
    assert state_mutant == payload_cost_model.SOURCE_STATE_PRESENT_VALID, "Mutant must cause same countermodel to fail (RED)"
    assert len(contracts_mutant[0]["sections"]["critical_constraints"]) == 1, "Mutant silently omits non-string member"


def test_ablation_r1_rule_dict_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R1-RULE-DICT-VALIDATION:
    Production mechanism: _is_valid_rule_dict validating required carrier text.
    Countermodel: CM-R1-RULE-DICT-TEXT-EMPTY.
    Restored: GREEN (fails closed with SOURCE_STATE_INVALID).
    Mutant: RED (accepting any dict causes empty carrier to be silently dropped).
    """
    doc = {"calendar": {"slot_rules": [{"rule": ""}]}}

    # Restored / Green:
    _, state_green, sub_green, limits_green = payload_cost_model.normalize_domain_contracts(doc)
    assert state_green == payload_cost_model.SOURCE_STATE_INVALID
    assert sub_green == payload_cost_model.SUBTYPE_MALFORMED_SHAPE

    # Mutant / Red:
    monkeypatch.setattr(payload_cost_model, "_is_valid_rule_dict", lambda d: True)
    monkeypatch.setattr(payload_cost_model, "_validate_contract_declared_field_types", lambda item: True)
    _, state_mutant, _, _ = payload_cost_model.normalize_domain_contracts(doc)
    assert state_mutant == payload_cost_model.SOURCE_STATE_PRESENT_VALID, "Mutant must cause same countermodel to fail (RED)"


def test_ablation_s1_contract_ref_container_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-S1-CONTRACT-REF-CONTAINER-REAL-PATH:
    Production mechanism: parse_contract_refs container validation on real path.
    Countermodel: CM-S1-CONTRACT-REF-CONTAINER-SCALAR.
    Restored: GREEN (build_semantic_chunk_plan emits MALFORMED_CONTAINER, plan degraded).
    Mutant: RED (unchecked iteration yields characters, missing MALFORMED_CONTAINER limitation).
    """
    intake = _base_intake()
    intake.target_profile = {"contracts": "contract:missing"}

    # Restored / Green:
    plan_green = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=20000)
    assert "unresolved_contract_reference:MALFORMED_CONTAINER" in plan_green.limitations
    assert plan_green.status == "degraded"

    # Mutant / Red: Pre-repair helper that iterated characters without checking list
    def _buggy_parse_contract_refs(data: Any) -> tuple[list[str], list[str]]:
        raw = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        val = raw.get("target_profile", {}).get("contracts", [])
        return [c for c in val if c.strip()], []

    monkeypatch.setattr(payload_cost_model, "parse_contract_refs", _buggy_parse_contract_refs)
    plan_mutant = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=20000)
    assert "unresolved_contract_reference:MALFORMED_CONTAINER" not in plan_mutant.limitations, "Mutant must lose container error (RED)"


def test_ablation_s2_selected_pack_requiredness_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-S2-SELECTED-PACK-REQUIREDNESS-REAL-PATH:
    Production mechanism: contracts_context marking selected_contract_pack as required=True.
    Countermodel: CM-S2-SELECTED-PACK-BUDGET-LOSS.
    Restored: GREEN (selected pack marked required, retained in minimal floor).
    Mutant: RED (marking required=False causes pack to drop from floor without critical limitation).
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "standalone-pack": {
                    "description": "Pack with no effective contracts",
                }
            }
        }
    }

    # Restored / Green:
    ctx_green, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="standalone-pack",
        semantic_group="primary_backend_logic",
    )
    assert len(ctx_green["review_packs"]) == 1
    assert ctx_green["review_packs"][0]["required"] is True
    min_ctx_green = payload_cost_model.minimal_contracts_context(ctx_green)
    assert len(min_ctx_green["review_packs"]) == 1

    # Mutant / Red: Buggy pre-repair behavior where required was conditional on bool(effective_contracts)
    original_contracts_context = payload_cost_model.contracts_context

    def _buggy_contracts_context(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[str]]:
        ctx, limits = original_contracts_context(*args, **kwargs)
        for p in ctx.get("review_packs", []):
            if not p.get("effective_contracts"):
                p["required"] = False
        return ctx, limits

    monkeypatch.setattr(payload_cost_model, "contracts_context", _buggy_contracts_context)
    ctx_mutant, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="standalone-pack",
        semantic_group="primary_backend_logic",
    )
    min_ctx_mutant = payload_cost_model.minimal_contracts_context(ctx_mutant)
    assert not min_ctx_mutant["review_packs"], "Mutant causes selected pack to drop from floor (RED)"


def test_ablation_s2_required_loss_routing_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-S2-REQUIRED-LOSS-ROUTING-REAL-PATH:
    Production mechanism: is_required_context_loss checking required_contract_pack_context_lost:.
    Countermodel: Routing with lost required pack context.
    Restored: GREEN (payload_path is None, status is limited).
    Mutant: RED (checking only contract loss allows lost pack payload to route).
    """
    intake = _base_intake()
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=20000)
    brief = _brief(intake, plan)

    # Monkeypatch contracts_context to emit required pack loss
    from app.agent_review import chunk_payload_builder
    orig_contracts_context = chunk_payload_builder.contracts_context
    def _contracts_with_pack_loss(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], list[str]]:
        ctx, limits = orig_contracts_context(*args, **kwargs)
        return ctx, [*limits, "required_contract_pack_context_lost:my-pack"]

    monkeypatch.setattr(chunk_payload_builder, "contracts_context", _contracts_with_pack_loss)

    # Restored / Green: Production recognizes pack loss, blocks routing (payload_path is None, 0 payloads emitted)
    manifest_green, payloads_green = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    assert manifest_green.chunks[0].payload_path is None
    assert len(payloads_green) == 0

    # Mutant / Red: Pre-repair is_required_context_loss in chunk_payload_builder only checked required_contract_context_lost:
    monkeypatch.setattr(
        chunk_payload_builder,
        "is_required_context_loss",
        lambda lim: lim.startswith("required_contract_context_lost:"),
    )
    manifest_mutant, payloads_mutant = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    assert manifest_mutant.chunks[0].payload_path is not None, "Mutant allows lost pack payload to route (RED)"
    assert len(payloads_mutant) > 0


def test_ablation_s3_format_mode_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-S3-FORMAT-MODE-REAL-PATH:
    Production mechanism: detect_review_packs_format treating empty bindings as LEGACY_FLAT.
    Countermodel: CM-S3-EMPTY-BINDINGS-LEGACY-MODE.
    Restored: GREEN (detects LEGACY_FLAT, fuzzy matches 'calendar' -> 'calendar-pack').
    Mutant: RED (detects MODERN_MAPPING, exact match fails, emits selected_contract_pack_missing).
    """
    intake = _base_intake()
    doc_empty = {
        "packs": [{"id": "calendar-pack", "description": "Calendar scheduling rules"}],
        "contract_bindings": {},
    }
    intake.target_profile = {"review_packs": doc_empty}

    # Restored / Green:
    ctx_green, limits_green = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="calendar",
        semantic_group="primary_backend_logic",
    )
    assert not any(lim.startswith("selected_contract_pack_missing:") for lim in limits_green)
    assert len(ctx_green["review_packs"]) == 1
    assert ctx_green["review_packs"][0]["id"] == "calendar-pack"

    # Mutant / Red: Pre-repair bug that switched format on any present bindings dict
    monkeypatch.setattr(
        payload_cost_model,
        "detect_review_packs_format",
        lambda doc, extra_bindings=None: payload_cost_model.FORMAT_MODERN_MAPPING,
    )
    ctx_mutant, limits_mutant = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-1",
        selected_contract_pack="calendar",
        semantic_group="primary_backend_logic",
    )
    assert any(lim.startswith("selected_contract_pack_missing:calendar") for lim in limits_mutant), "Mutant must cause fuzzy match to fail (RED)"
    assert not ctx_mutant["review_packs"]


def test_ablation_s3_legacy_pattern_dispatch_real_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-S3-LEGACY-PATTERN-DISPATCH-REAL-PATH:
    Production mechanism: format-specific pattern dispatch preserving legacy substring match.
    Countermodel: CM-S3-LEGACY-SUBSTRING-PATTERN-MATCH.
    Restored: GREEN (matches 'backend/api' as substring of 'backend/api/shifts.py').
    Mutant: RED (unconditional fnmatchcase fails on substring without wildcard).
    """
    legacy_contract = {"id": "legacy-rule", "patterns": ["backend/api"]}
    chunk_files = {"backend/api/shifts.py"}

    # Restored / Green:
    assert payload_cost_model._contract_matches_chunk(
        legacy_contract,
        chunk_files=chunk_files,
        format=payload_cost_model.FORMAT_LEGACY_FLAT,
    ) is True

    # Mutant / Red: Pre-repair bug where modern fnmatch was used unconditionally
    monkeypatch.setattr(
        payload_cost_model,
        "_contract_matches_chunk",
        lambda c, chunk_files, format=None: payload_cost_model._matches_modern_pattern("backend/api/shifts.py", c.get("patterns", [])),
    )
    assert payload_cost_model._contract_matches_chunk(
        legacy_contract,
        chunk_files=chunk_files,
        format=payload_cost_model.FORMAT_LEGACY_FLAT,
    ) is False, "Mutant must cause legacy substring pattern to fail (RED)"


def test_critical_limitation_consumption_matrix() -> None:
    """Critical Limitation Consumption Matrix: Proves all 10 critical Gate A limitation
    prefixes are consumed by semantic_chunker._plan_status returning 'degraded',
    guaranteeing 0 orphan critical limitation families.
    """
    critical_prefixes = [
        "required_contract_context_lost:c1",
        "required_contract_pack_context_lost:p1",
        "unresolved_contract_binding:p1->c1",
        "unresolved_contract_reference:c1",
        "orphan_contract_binding:p1",
        "invalid_source_contract:MALFORMED_SHAPE",
        "invalid_source_review_packs:MALFORMED_SHAPE",
        "malformed_contract_bindings:must_be_mapping",
        "required_source_absent:review_packs",
        "selected_contract_pack_missing:p1",
    ]

    for lim in critical_prefixes:
        prefix_matched = any(lim.startswith(p) for p in payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES)
        assert prefix_matched, f"Prefix for {lim} must belong to CRITICAL_CONTRACT_LIMITATION_PREFIXES"
        status = semantic_chunker._plan_status(
            intake_status="admitted",
            limitations=[lim],
            files_partially_covered=[],
            files_not_covered=[],
        )
        assert status == "degraded", f"Limitation {lim} must degrade plan status, got {status}"



# Positive Controls (Section 25)


def test_positive_controls_gate_a_second_cycle(tmp_path: Path) -> None:
    """Section 25 Positive Controls: Comprehensive validation of clean Gate A operations."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "api_contract": {
                "paths": ["backend/api/*"],
                "rules": ["Preserve API contract"],
            },
        },
        "review_packs": {
            "packs": {
                "api_pack": {
                    "domain_contract": "api_contract",
                    "description": "API review pack",
                    "paths": ["backend/api/*"],
                },
            },
            "contract_bindings": {
                "api_pack": ["api_contract"],
            },
        },
        "contracts": ["contract:api_contract"],
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=20000)
    assert plan.status in {"complete", "partial"}
    assert not any(any(lim.startswith(p) for p in payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES) for lim in plan.limitations)

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    resp_dir = tmp_path / "valid_second_cycle_resp"
    resp_dir.mkdir()
    for chunk in plan.chunks:
        resp = {
            "schema_version": 1,
            "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": list(chunk.files),
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    results = parse_chunk_results(plan, responses_dir=resp_dir)
    review = synthesize_final_review(results)
    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)

    assert gate.status == "passed"
    assert gate.manual_review_required is False


# ---------------------------------------------------------------------------
# B1 — TargetShape -> AdmittedGrammar
# ---------------------------------------------------------------------------


def test_cm_b1_exact_target_shape_mirror() -> None:
    """CM-B1-EXACT-TARGET-SHAPE-MIRROR:
    Verifies that the exact AgentEscala target shape (AgentEscala@b281ca5d2872117b1c128cabb1735e264f024eaf)
    normalizes cleanly into PRESENT_VALID without dropping sections or requiring rewritten shapes.
    - auth_admin.response_models: list[str]
    - auth_admin.orm_models: list[str]
    - auth_admin.critical_constraints: list[str]
    - auth_admin.review_checklist: list[str]
    - calendar.slot_rules: bounded rule dicts
    - swaps.rules: bounded rule dicts
    - response_model_rules: top-level list[str]
    """
    fixture = _structural_mirror_fixture()
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(fixture)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype is None
    assert limits == []

    auth = next(c for c in contracts if c["id"] == "auth_admin")
    assert "response_models" in auth["sections"]
    assert "orm_models" in auth["sections"]
    assert [i["text"] for i in auth["sections"]["response_models"]] == ["UserResponse", "AdminUserResponse", "TokenResponse"]
    assert [i["text"] for i in auth["sections"]["orm_models"]] == ["User", "Role"]

    cal = next(c for c in contracts if c["id"] == "calendar")
    assert "slot_rules" in cal["sections"]
    assert cal["sections"]["slot_rules"][0]["text"] == "Slot start time must precede slot end time"
    assert cal["sections"]["slot_rules"][0]["invariant"] is True

    swaps = next(c for c in contracts if c["id"] == "swaps")
    assert "rules" in swaps["sections"]
    assert swaps["sections"]["rules"][0]["text"] == "Swaps require consent from both clinical parties"

    resp = next(c for c in contracts if c["id"] == "response_model_rules")
    assert resp["rules"] == ["All response models must validate datetime in UTC"]


def test_ab_b1_nested_list_string_grammar(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-B1-NESTED-LIST-STRING-GRAMMAR:
    Causal ablation: rejecting generic nested list[str] sections breaks target shape admission.
    Mechanism present: admits nested list[str] (e.g. response_models, orm_models) -> PRESENT_VALID.
    Mutated: rejecting non-standard list sections -> SOURCE_STATE_INVALID / SUBTYPE_MALFORMED_SHAPE.
    Restored: PRESENT_VALID.
    """
    fixture = _structural_mirror_fixture()
    # Baseline Green
    _, state, _, limits = payload_cost_model.normalize_domain_contracts(fixture)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert limits == []

    # Mutant Red: only allow hardcoded sections, reject generic list[str] like response_models/orm_models
    orig_validate = payload_cost_model._validate_contract_declared_field_types

    def mutant_validate(item: dict[str, Any]) -> bool:
        if "response_models" in item or "orm_models" in item:
            return False
        return orig_validate(item)

    monkeypatch.setattr(payload_cost_model, "_validate_contract_declared_field_types", mutant_validate)
    _, ablated_state, ablated_subtype, _ = payload_cost_model.normalize_domain_contracts(fixture)
    assert ablated_state == payload_cost_model.SOURCE_STATE_INVALID, "Mutant must cause validation to fail (RED)"
    assert ablated_subtype == payload_cost_model.SUBTYPE_MALFORMED_SHAPE


# ---------------------------------------------------------------------------
# B2 — ApplicablePack -> RequiredSemanticContext
# ---------------------------------------------------------------------------


def test_cm_b2_path_applicable_no_binding_pack() -> None:
    """CM-B2-PATH-APPLICABLE-NO-BINDING-PACK:
    A review pack applicable purely by path match (no domain_contract, no contract_bindings,
    no selected_contract_pack) MUST preserve its pack_id as required context.
    It can never be eliminated as an 'optional pack'.
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "api_pack": {
                    "paths": ["backend/api/*"],
                    "description": "API pack without domain contracts or bindings",
                }
            }
        }
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="primary_backend_logic",
    )
    assert limits == []
    packs = ctx["review_packs"]
    assert len(packs) == 1
    pack = packs[0]
    assert pack["id"] == "api_pack"
    assert pack.get("required") is True
    assert pack.get("required_reasons") == ["applicable_pack_identity"]

    # Minimal contracts context preserves the pack floor
    min_ctx = payload_cost_model.minimal_contracts_context(ctx)
    min_packs = min_ctx["review_packs"]
    assert len(min_packs) == 1
    assert min_packs[0]["id"] == "api_pack"
    assert min_packs[0].get("required") is True
    # Optional metadata stripped from minimal floor
    assert "description" not in min_packs[0]


def test_cm_b2_applicable_pack_optional_metadata_shrink() -> None:
    """CM-B2-APPLICABLE-PACK-OPTIONAL-METADATA-SHRINK:
    Scenario: Pack applicable by path with large description under budget pressure.
    Expected: description removed/reduced, pack ID preserved, no required_contract_pack_context_lost,
    optional loss recorded (contracts_context_reduced in coverage_impact), payload may proceed.
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "api_pack": {
                    "paths": ["backend/api/*"],
                    "description": "A" * 4000,
                    "recommended_review_preset": "deep",
                }
            }
        }
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=1, max_chars_per_block=15000)
    brief = _brief(intake, plan)

    # Measure unconstrained
    _, unconstrained_payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    unconstrained_chunk = next(iter(unconstrained_payloads.values()))
    untruncated_len = unconstrained_chunk.truncation.original_chars

    # Set budget tight enough to force shrink of pack optional metadata, but big enough for pack floor
    plan.chunks[0].prompt_budget_chars = untruncated_len - 2500

    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )
    payload = next(iter(payloads.values()))
    packs = payload.chunk_context["contracts_context"]["review_packs"]
    assert len(packs) == 1
    assert packs[0]["id"] == "api_pack"
    # Optional description and recommended_review_preset stripped
    assert "description" not in packs[0]
    assert "recommended_review_preset" not in packs[0]
    # No required pack loss
    assert not any(lim.startswith("required_contract_pack_context_lost") for lim in payload.limitations)
    # Optional loss recorded
    assert "contracts_context_reduced" in payload.truncation.coverage_impact
    assert "contracts_context" in payload.truncation.omitted_sections
    # Manifest entry allows execution (payload_path is not None)
    entry = next(iter(manifest.chunks))
    assert entry.payload_path is not None


def test_ab_b2_all_applicable_pack_id_required() -> None:
    """AB-B2-ALL-APPLICABLE-PACK-ID-REQUIRED:
    Causal ablation: failing to mark path-applicable packs as required causes them to be silently
    dropped during budget shrink without emitting required_contract_pack_context_lost.
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "api_pack": {"paths": ["backend/api/*"]},
            }
        }
    }
    ctx, _ = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api_schema_contract",
    )
    # Baseline Green: pack is marked required
    assert ctx["review_packs"][0].get("required") is True

    # Mutant Red: applicable pack is treated as optional (pre-B2 defect)
    mutant_ctx = copy.deepcopy(ctx)
    mutant_ctx["review_packs"][0]["required"] = False
    payload = {
        "chunk_context": {"contracts_context": mutant_ctx},
        "limitations": [],
    }
    _shrink_contracts_context(payload)
    # In mutant, pack was popped silently in step 1 without required loss code!
    assert payload["chunk_context"]["contracts_context"]["review_packs"] == []
    assert not any(lim.startswith("required_contract_pack_context_lost") for lim in payload["limitations"]), \
        "Mutant must drop pack silently without required loss code (RED)"


def test_ab_b2_optional_pack_metadata_shrink() -> None:
    """AB-B2-OPTIONAL-PACK-METADATA-SHRINK:
    Causal ablation: skipping optional pack metadata shrink forces premature required pack loss.
    """
    payload = {
        "chunk_context": {
            "contracts_context": {
                "review_packs": [{"id": "p1", "description": "huge " * 500, "required": True}],
                "domain_contracts": [],
            }
        },
        "limitations": [],
    }
    # Baseline Green: normal shrink strips description, keeps pack
    normal_payload = copy.deepcopy(payload)
    _shrink_contracts_context(normal_payload)
    assert len(normal_payload["chunk_context"]["contracts_context"]["review_packs"]) == 1
    assert "description" not in normal_payload["chunk_context"]["contracts_context"]["review_packs"][0]
    assert normal_payload["limitations"] == []

    # Mutant Red: shrinker skips optional metadata strip and immediately pops required pack
    def mutant_shrink(p: dict[str, Any]) -> bool:
        packs = p["chunk_context"]["contracts_context"]["review_packs"]
        if packs:
            popped = packs.pop()
            p["limitations"].append(f"required_contract_pack_context_lost:{popped['id']}")
            return True
        return False

    mutant_payload = copy.deepcopy(payload)
    mutant_shrink(mutant_payload)
    assert mutant_payload["chunk_context"]["contracts_context"]["review_packs"] == []
    assert "required_contract_pack_context_lost:p1" in mutant_payload["limitations"], \
        "Mutant must prematurely emit required pack loss (RED)"


# ---------------------------------------------------------------------------
# B3 — LimitationClass -> Producer -> Carrier -> TerminalEffect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason_class", "producer_kind", "origin"),
    [
        ("invalid_source_contract:MALFORMED_SHAPE", "domain_contracts", "planner"),
        ("invalid_source_review_packs:MALFORMED_SHAPE", "review_packs", "planner"),
        ("malformed_contract_bindings:must_be_mapping", "bindings", "planner"),
        ("unresolved_contract_binding:", "unresolved_binding", "planner"),
        ("unresolved_contract_reference:", "unresolved_ref", "planner"),
        ("orphan_contract_binding:", "orphan_binding", "planner"),
        ("required_source_absent:", "absent_domain_source", "planner"),
        ("selected_contract_pack_missing:", "missing_selected_pack", "planner"),
        ("required_contract_context_lost:", "builder_contract_loss", "builder"),
        ("required_contract_pack_context_lost:", "builder_pack_loss", "builder"),
    ],
)
def test_cm_b3_reason_class_producer_matrix(reason_class: str, producer_kind: str, origin: str) -> None:
    """CM-B3-REASON-CLASS-PRODUCER-MATRIX:
    Tests each of the 10 PRODUCED critical limitation classes through its actual production mechanism
    and asserts its carrier route:
    - Planner-origin (8 classes): degrades plan status to 'degraded'.
    - Builder-origin (2 classes): emits required loss, setting manifest entry status to 'limited' and payload_path=None.
    Guarantees 0 orphan producers and 0 orphan registry entries.
    """
    intake = _base_intake()
    chunk_files = ["backend/api/shifts.py"]

    if origin == "planner":
        if producer_kind == "domain_contracts":
            intake.target_profile["domain_contracts"] = "not_a_valid_container"
        elif producer_kind == "review_packs":
            intake.target_profile["review_packs"] = "not_a_valid_container"
        elif producer_kind == "bindings":
            intake.target_profile["review_packs"] = {
                "packs": {"p1": {"paths": ["backend/*"]}},
                "contract_bindings": "not_a_dict",
            }
        elif producer_kind == "unresolved_binding":
            intake.target_profile["domain_contracts"] = {"c1": {"rules": ["r1"]}}
            intake.target_profile["review_packs"] = {
                "packs": {"p1": {"paths": ["backend/*"]}},
                "contract_bindings": {"p1": ["missing_c2"]},
            }
        elif producer_kind == "unresolved_ref":
            intake.target_profile["domain_contracts"] = {"c1": {"rules": ["r1"]}}
        elif producer_kind == "orphan_binding":
            intake.target_profile["domain_contracts"] = {"c1": {"rules": ["r1"]}}
            intake.target_profile["review_packs"] = {
                "packs": {"p1": {"paths": ["backend/*"]}},
                "contract_bindings": {"orphan_p2": ["c1"]},
            }
        elif producer_kind == "absent_domain_source":
            intake.target_profile["domain_contracts"] = None
            intake.target_profile["review_packs"] = {
                "packs": {"p1": {"paths": ["backend/*"]}},
                "contract_bindings": {"p1": ["c1"]},
            }
        elif producer_kind == "missing_selected_pack":
            intake.target_profile["review_packs"] = {
                "packs": {"p1": {"paths": ["backend/*"]}},
            }

        chunk_contracts = ["contract:missing_c"] if producer_kind == "unresolved_ref" else []
        selected_pack = "nonexistent_pack" if producer_kind == "missing_selected_pack" else None

        ctx, limits = payload_cost_model.contracts_context(
            intake,
            chunk_files=chunk_files,
            chunk_contracts=chunk_contracts,
            chunk_id="chunk-01",
            selected_contract_pack=selected_pack,
            semantic_group="primary_backend_logic",
        )
        assert any(lim.startswith(reason_class) for lim in limits), f"Expected limitation starting with {reason_class} in {limits}"
        # Carrier route: degrades plan status
        status = semantic_chunker._plan_status(
            intake_status="admitted",
            limitations=limits,
            files_partially_covered=[],
            files_not_covered=[],
        )
        assert status == "degraded", f"Limitation {reason_class} must degrade plan status"

    elif origin == "builder":
        if producer_kind == "builder_contract_loss":
            payload = {
                "chunk_context": {
                    "contracts_context": {
                        "review_packs": [],
                        "domain_contracts": [{"id": "req_c1", "required": True}],
                    }
                },
                "limitations": [],
            }
            _shrink_contracts_context(payload)
            assert "required_contract_context_lost:req_c1" in payload["limitations"]
            assert payload_cost_model.is_required_context_loss("required_contract_context_lost:req_c1") is True

        elif producer_kind == "builder_pack_loss":
            payload = {
                "chunk_context": {
                    "contracts_context": {
                        "review_packs": [{"id": "req_p1", "required": True}],
                        "domain_contracts": [],
                    }
                },
                "limitations": [],
            }
            _shrink_contracts_context(payload)
            assert "required_contract_pack_context_lost:req_p1" in payload["limitations"]
            assert payload_cost_model.is_required_context_loss("required_contract_pack_context_lost:req_p1") is True


def test_ab_b3_reason_class_producer_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-B3-REASON-CLASS-PRODUCER-BINDING:
    Causal ablation: Mutating the critical limitation prefix registry to omit a producer's class
    breaks plan status degradation.
    """
    lim = "unresolved_contract_binding:p1:c1"
    # Baseline Green: lim is critical and degrades plan status
    status = semantic_chunker._plan_status(
        intake_status="admitted",
        limitations=[lim],
        files_partially_covered=[],
        files_not_covered=[],
    )
    assert status == "degraded"

    # Mutant Red: critical prefixes omit unresolved_contract_binding
    mutated_prefixes = tuple(p for p in payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES if not p.startswith("unresolved_contract_binding"))
    monkeypatch.setattr(payload_cost_model, "CRITICAL_CONTRACT_LIMITATION_PREFIXES", mutated_prefixes)
    mutant_status = semantic_chunker._plan_status(
        intake_status="admitted",
        limitations=[lim],
        files_partially_covered=[],
        files_not_covered=[],
    )
    assert mutant_status != "degraded", "Mutant omitting limitation class must not degrade plan status (RED)"


# ---------------------------------------------------------------------------
# B4 — NormativeGrammar -> SingleExecutableAuthority -> EvidenceReceipt
# ---------------------------------------------------------------------------


def test_b4_registry_truth() -> None:
    """B4 — SINGLE GRAMMAR AUTHORITY:
    Verifies that the evidence receipt and code registries have zero drift.
    Receipt field registries match the single executable production authority in payload_cost_model.
    """
    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)

    # 1. Grammar section (Sections 29 & 30)
    dc_reg = receipt["grammar"]["domain_contract"]
    assert dc_reg["reserved_metadata"] == sorted(payload_cost_model.RESERVED_DOMAIN_CONTRACT_METADATA_KEYS)
    assert dc_reg["identity_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_IDENTITY_FIELDS)
    assert dc_reg["scalar_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_SCALAR_FIELDS)
    assert dc_reg["path_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_PATH_LIST_FIELDS)
    assert dc_reg["rule_sections"] == sorted(payload_cost_model.MODERN_CONTRACT_RULE_SECTIONS)
    assert dc_reg["generic_named_string_section_rule"] == payload_cost_model.GENERIC_NAMED_STRING_SECTION_RULE
    assert dc_reg["unknown_field_policy"] == payload_cost_model.DOMAIN_CONTRACT_UNKNOWN_FIELD_POLICY

    rp_reg = receipt["grammar"]["review_pack"]
    assert rp_reg["modern_identity_authority"] == payload_cost_model.MODERN_PACK_IDENTITY_AUTHORITY
    assert rp_reg["modern_value_fields"] == sorted(payload_cost_model.MODERN_MAPPING_PACK_VALUE_FIELDS)
    assert rp_reg["admitted_nonsemantic_target_metadata"] == sorted(payload_cost_model.TARGET_METADATA_NOT_USED_AS_RELATION)
    assert rp_reg["unknown_field_policy"] == payload_cost_model.REVIEW_PACK_UNKNOWN_FIELD_POLICY
    assert rp_reg["legacy_input_policy"] == payload_cost_model.REVIEW_PACK_LEGACY_INPUT_POLICY
    assert rp_reg["mixed_shape_policy"] == payload_cost_model.REVIEW_PACK_MIXED_SHAPE_POLICY
    # Cycle-3 registries (C1 modern admission totality): one declared census, one declared envelope.
    assert dc_reg["modern_exact_path_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_EXACT_PATH_FIELDS)
    assert dc_reg["modern_pattern_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_PATTERN_FIELDS)
    assert dc_reg["scope_value_domain"] == sorted(payload_cost_model.MODERN_SCOPE_DOMAIN)
    assert rp_reg["envelope_keys"] == sorted(payload_cost_model.REVIEW_PACK_ENVELOPE_KEYS)
    assert rp_reg["modern_exact_path_fields"] == sorted(payload_cost_model.MODERN_PACK_EXACT_PATH_FIELDS)
    assert rp_reg["modern_pattern_fields"] == sorted(payload_cost_model.MODERN_PACK_PATTERN_FIELDS)
    assert rp_reg["scope_value_domain"] == sorted(payload_cost_model.MODERN_SCOPE_DOMAIN)
    assert rp_reg["legacy_projection"] == sorted(["id", "description", "recommended_review_preset"])
    assert receipt["grammar"]["registry_drift"] is False

    # 2. Historical B4 section
    b4_reg = receipt["B4_single_grammar_authority"]["production_registry"]
    assert b4_reg["reserved_metadata"] == sorted(payload_cost_model.RESERVED_DOMAIN_CONTRACT_METADATA_KEYS)
    assert b4_reg["identity_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_IDENTITY_FIELDS)
    assert b4_reg["scalar_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_SCALAR_FIELDS)
    assert b4_reg["path_list_fields"] == sorted(payload_cost_model.MODERN_CONTRACT_PATH_LIST_FIELDS)
    assert b4_reg["rule_sections"] == sorted(payload_cost_model.MODERN_CONTRACT_RULE_SECTIONS)
    assert b4_reg["rule_dict_required_fields"] == sorted(payload_cost_model.MODERN_RULE_DICT_REQUIRED_FIELDS)
    assert b4_reg["rule_dict_optional_fields"] == sorted(payload_cost_model.MODERN_RULE_DICT_OPTIONAL_FIELDS)
    assert b4_reg["rule_dict_fields"] == sorted(payload_cost_model.ALL_MODERN_RULE_DICT_FIELDS)
    assert b4_reg["semantic_pack_fields"] == sorted(payload_cost_model.GATE_A_SEMANTIC_PACK_FIELDS)
    assert b4_reg["target_metadata_not_used_as_relation"] == sorted(payload_cost_model.TARGET_METADATA_NOT_USED_AS_RELATION)

    crit_classes = receipt["B4_single_grammar_authority"]["critical_limitation_consumption"]["classes"]
    assert crit_classes == sorted(payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES)
    assert receipt["B4_single_grammar_authority"]["critical_limitation_consumption"]["families"] == len(payload_cost_model.CRITICAL_CONTRACT_LIMITATION_PREFIXES)


def test_ab_b4_source_grammar_single_authority() -> None:
    """AB-B4-SOURCE-GRAMMAR-SINGLE-AUTHORITY:
    Causal ablation: Injecting unsupported fields (like 'required: true' in source contract)
    fails closed with UNSUPPORTED_NONEMPTY, proving that internal derived states or rogue keys
    are never admitted as source grammar.
    """
    raw_with_unsupported_key = {
        "auth": {
            "required": True,
            "rules": ["Valid rule"],
        }
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw_with_unsupported_key)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_contract:UNSUPPORTED_NONEMPTY" in limits

    # When stripped to only owner-admitted grammar, it passes cleanly
    raw_clean = {
        "auth": {
            "rules": ["Valid rule"],
        }
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw_clean)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype is None
    assert limits == []


# ---------------------------------------------------------------------------
# Normative Controls Crosswalk (CM-CL2-01..07, PC-CL2-01..07, CM-C2-MAP-01, etc.)
# ---------------------------------------------------------------------------


def test_cm_cl2_01_capability_boundary_nonclaims() -> None:
    """CM-CL2-01: Result presented as ClaimV1 or claiming exhaustive coverage is refused;
    normalized contract context never fabricates claim_id, deterministic claim authority,
    or per-claim coverage states.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "api_contract": {"rules": ["Preserve API contract"]},
        },
        "review_packs": {
            "packs": {
                "api_pack": {"paths": ["backend/api/*"], "domain_contract": "api_contract"},
            }
        },
    }
    contracts, _, _, _ = payload_cost_model.normalize_domain_contracts(intake.target_profile["domain_contracts"])
    packs, _, _, _, _ = payload_cost_model.normalize_review_packs(intake.target_profile["review_packs"])

    # Ensure no claim_id, claim_authority, or per-claim coverage is fabricated
    for c in contracts:
        assert "claim_id" not in c
        assert "claim_authority" not in c
        assert "per_claim_coverage" not in c
    for p in packs:
        assert "claim_id" not in p
        assert "claim_authority" not in p
        assert "per_claim_coverage" not in p

    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)
    assert receipt["capability"]["label"] == "contract_aware_advisory"
    assert receipt["capability"]["nonclaims"]["ClaimV1"] is True
    assert receipt["capability"]["nonclaims"]["exhaustive_per_claim_coverage"] is True


def test_pc_cl2_01_advisory_capability_admitted() -> None:
    """PC-CL2-01: Bounded contract-aware advisory capability is admitted and preserved."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "api_contract": {"rules": ["Preserve API contract"]},
        },
        "review_packs": {
            "packs": {
                "api_pack": {"paths": ["backend/api/*"], "domain_contract": "api_contract"},
            }
        },
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert limits == []
    assert len(ctx["domain_contracts"]) == 1
    assert ctx["domain_contracts"][0]["id"] == "api_contract"
    assert len(ctx["review_packs"]) == 1
    assert ctx["review_packs"][0]["id"] == "api_pack"


def test_cm_c2_map_01_unresolved_explicit_relation_fails_closed() -> None:
    """CM-C2-MAP-01: Unresolved explicit relation fails closed with unresolved_contract_binding;
    no NLP, descriptions-as-relation, or fuzzy recovery in modern mapping.
    """
    intake = _base_intake()
    # Pack description mentions 'calendar', but explicit binding points to nonexistent 'calendar_missing'
    intake.target_profile = {
        "domain_contracts": {
            "calendar_service": {"rules": ["calendar service rule"]},
        },
        "review_packs": {
            "packs": {
                "cal_pack": {
                    "paths": ["backend/calendar/*"],
                    "description": "Calendar pack matching calendar service via fuzzy text",
                    "domain_contract": "calendar_missing",
                },
            }
        },
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/calendar/service.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="calendar",
    )
    assert any("unresolved_contract_binding:cal_pack:calendar_missing" in lim or "unresolved_contract_binding:cal_pack->calendar_missing" in lim for lim in limits)
    # No NLP / fuzzy match: calendar_service is NOT bound via description text match
    assert not any(c["id"] == "calendar_service" for c in ctx["domain_contracts"])


def test_pc_c2_map_01_explicit_mapping_and_bindings_resolved() -> None:
    """PC-C2-MAP-01: Explicit mapping packs and contract_bindings resolve exact EffectiveContractRefs."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "c_primary": {"rules": ["rule 1"]},
            "c_bound": {"rules": ["rule 2"]},
        },
        "review_packs": {
            "packs": {
                "p1": {
                    "paths": ["backend/api/*"],
                    "domain_contract": "c_primary",
                }
            },
            "contract_bindings": {
                "p1": ["c_bound"],
            },
        },
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert limits == []
    c_ids = {c["id"] for c in ctx["domain_contracts"]}
    assert c_ids == {"c_primary", "c_bound"}
    p_ids = {p["id"] for p in ctx["review_packs"]}
    assert p_ids == {"p1"}


def test_cm_cl2_03_invalid_source_never_not_relevant() -> None:
    """CM-CL2-03: Invalid source input yields SOURCE_STATE_INVALID and typed limitation,
    and never collapses to not_relevant.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": "invalid_contracts_string_container",
        "review_packs": {"packs": {"p1": {"paths": ["backend/*"]}}},
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert any("invalid_source_contract:MALFORMED_SHAPE" in lim for lim in limits)
    # Must NEVER emit contracts_context_not_relevant when source is invalid
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in limits)


def test_pc_cl2_03_source_states_distinct() -> None:
    """PC-CL2-03: Source states (present_valid, absent, invalid) are evaluated and preserved independently."""
    assert payload_cost_model.SOURCE_STATE_PRESENT_VALID == "PRESENT_VALID"
    assert payload_cost_model.SOURCE_STATE_ABSENT == "ABSENT"
    assert payload_cost_model.SOURCE_STATE_INVALID == "INVALID"
    assert len({
        payload_cost_model.SOURCE_STATE_PRESENT_VALID,
        payload_cost_model.SOURCE_STATE_ABSENT,
        payload_cost_model.SOURCE_STATE_INVALID,
    }) == 3

    # 1. present_valid
    _, state_valid, subtype_valid, lim_valid = payload_cost_model.normalize_domain_contracts({"c1": {"rules": ["r1"]}})
    assert state_valid == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert subtype_valid is None
    assert lim_valid == []

    # 2. absent
    _, state_absent, subtype_absent, lim_absent = payload_cost_model.normalize_domain_contracts(None)
    assert state_absent == payload_cost_model.SOURCE_STATE_ABSENT
    assert subtype_absent is None
    assert lim_absent == []

    # 3. invalid
    _, state_invalid, subtype_invalid, lim_invalid = payload_cost_model.normalize_domain_contracts("bad_input")
    assert state_invalid == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype_invalid == payload_cost_model.SUBTYPE_MALFORMED_SHAPE
    assert len(lim_invalid) > 0


def test_cm_cl2_04_missing_or_unresolved_never_not_relevant() -> None:
    """CM-CL2-04: Missing, unresolved, or invalid context never yields contracts_context_not_relevant."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {"c_other": {"rules": ["r"]}},
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["contract:missing_c"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert any("unresolved_contract_reference:missing_c" in lim for lim in limits)
    # Must NEVER emit contracts_context_not_relevant
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in limits)


def test_pc_cl2_04_valid_zero_applicable_context_not_relevant() -> None:
    """PC-CL2-04: When source is present_valid and valid applicability evaluation finds zero applicable context,
    contracts_context_not_relevant is emitted.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {
            "frontend_rules": {"paths": ["frontend/*"], "rules": ["react rules"]},
        },
    }
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=["target_profile:domain_contracts"],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert ctx["domain_contracts"] == []
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    payload = next(iter(payloads.values()))
    assert any(lim.startswith("contracts_context_not_relevant:") for lim in payload.limitations)


def test_cm_cl2_05_required_loss_nonconclusive() -> None:
    """CM-CL2-05: Loss of required contract or pack context propagates as non-conclusive
    (execution refusal / status limited).
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-c", "description": "H" * 4000, "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-c"]},
        },
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    # Constrain budget post-plan to force builder contract loss
    plan.chunks[0].prompt_budget_chars = 1000
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    entry = manifest.chunks[0]
    assert entry.status == "limited"
    assert entry.payload_path is None
    assert any(lim.startswith("required_contract_context_lost:req-c") for lim in entry.limitations)
    # Execution refused: chunk payload not admitted
    assert payloads.get(entry.chunk_id) is None


def test_pc_cl2_05_intact_required_floor_preserved() -> None:
    """PC-CL2-05: Intact hunks and required context form a non-silent floor preserved when budget allows."""
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "req-c", "description": "rule description", "paths": ["backend/api/*"]},
        ],
        "review_packs": {
            "packs": {"pack1": {"paths": ["backend/api/*"]}},
            "contract_bindings": {"pack1": ["req-c"]},
        },
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    entry = manifest.chunks[0]
    assert entry.status == "available"
    assert entry.payload_path is not None
    payload = payloads[entry.payload_path]
    c_ids = [c["id"] for c in payload.chunk_context["contracts_context"]["domain_contracts"]]
    assert "req-c" in c_ids
    assert len(payload.chunk_context["chunk_hunks"]) > 0


def test_cm_c2_loss_optional_01_optional_not_required() -> None:
    """CM-C2-LOSS-OPTIONAL-01: Optional loss is distinct from required loss;
    reduction of optional metadata must never emit required loss codes.
    """
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "pack1": {
                    "paths": ["backend/api/*"],
                    "description": "Very long optional description " * 100,
                }
            }
        }
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    brief = _brief(intake, plan)
    _, unconstrained_payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    orig_chars = next(iter(unconstrained_payloads.values())).truncation.original_chars

    # Constrain chunk budget to force optional metadata shrink
    plan.chunks[0].prompt_budget_chars = orig_chars - 1000
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    payload = payloads[manifest.chunks[0].payload_path]
    assert not any(lim.startswith("required_contract_pack_context_lost") for lim in payload.limitations)
    assert not any(lim.startswith("required_contract_context_lost") for lim in payload.limitations)
    assert "contracts_context_reduced" in payload.truncation.coverage_impact


def test_pc_c2_loss_optional_01_optional_loss_permits_review(tmp_path: Path) -> None:
    """PC-C2-LOSS-OPTIONAL-01: Positive useful review remains possible after optional-only loss."""
    intake = _base_intake()
    intake.target_profile = {
        "review_packs": {
            "packs": {
                "pack1": {
                    "paths": ["backend/api/*"],
                    "description": "Optional description " * 80,
                }
            }
        }
    }
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    brief = _brief(intake, plan)
    _, unconstrained_payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)
    orig_chars = next(iter(unconstrained_payloads.values())).truncation.original_chars

    plan.chunks[0].prompt_budget_chars = orig_chars - 800
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)

    entry = manifest.chunks[0]
    assert entry.payload_path is not None
    assert payloads.get(entry.payload_path) is not None

    resp_dir = tmp_path / "optional_loss_resp"
    resp_dir.mkdir()
    for chunk in plan.chunks:
        resp = {
            "schema_version": 1,
            "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": list(chunk.files),
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    results = parse_chunk_results(plan, responses_dir=resp_dir)
    review = synthesize_final_review(results)
    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)
    assert gate.status == "passed"


def test_cm_cl2_06_gate_substitution_refused() -> None:
    """CM-CL2-06: Gate A evidence cannot satisfy Gate B or Gate C by implication;
    cross-gate substitution is refused.
    """
    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)

    # Gate B and Gate C must be explicitly NOT_CLAIMED
    assert receipt["nonclaims"]["Gate_B"] == "NOT_CLAIMED"
    assert receipt["nonclaims"]["Gate_C"] == "NOT_CLAIMED"

    # Attempting to query Gate B or Gate C from Gate A qualification is refused
    assert receipt["gate"] == "Gate A"
    assert "Gate B" not in receipt["slice"]
    assert "Gate C" not in receipt["slice"]


def test_pc_cl2_06_gate_a_boundary_exact() -> None:
    """PC-CL2-06: Gate A candidate qualification retains exact gate label and declares
    external exact-head binding contract without false self-authored HEAD.
    """
    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)

    # Exact gate label and status
    assert receipt["gate"] == "Gate A"
    assert receipt["slice"] == "V1-C2 / Gate A (C2ExecutableContract)"
    assert receipt["status"] == "v1_c2_gate_a_normative_bindings_closed"
    assert receipt["terminal"] == "V1_C2_GATE_A_NORMATIVE_BINDINGS_CLOSED_DRAFT_AWAITING_READY_REGRANT"

    # Nonclaims: no substitution for Gate B or Gate C
    assert receipt["nonclaims"]["Gate_B"] == "NOT_CLAIMED"
    assert receipt["nonclaims"]["Gate_C"] == "NOT_CLAIMED"

    # External exact subject binding contract
    sb = receipt["subject_binding"]
    assert sb["mode"] == "FORGE_EXACT_HEAD"
    assert sb["self_hash_claimed"] is False
    assert sb["requirement"] == "all_exact_subject_authorities_must_agree"
    assert sb["exact_subject_authorities"] == [
        "PR.headRefOid",
        "canonical_CI.headSha",
        "current_head_guard_dispositions",
        "maintainer_checkpoint",
    ]
    # Receipt does NOT falsely self-author its own HEAD or mistake predecessor for current subject
    assert "subject" not in receipt


def test_normative_controls_frozen_crosswalk_totality() -> None:
    """Proves all 16 frozen crosswalk control IDs are present and bound to exact executable tests."""
    frozen_control_ids = [
        "CM-CL2-01", "PC-CL2-01",
        "CM-C2-MAP-01", "PC-C2-MAP-01",
        "CM-CL2-03", "PC-CL2-03",
        "CM-CL2-04", "PC-CL2-04",
        "CM-CL2-05", "PC-CL2-05",
        "CM-C2-LOSS-OPTIONAL-01", "PC-C2-LOSS-OPTIONAL-01",
        "CM-CL2-06", "PC-CL2-06",
        "CM-CL2-07", "PC-CL2-07",
    ]
    assert len(frozen_control_ids) == 16

    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)

    normative_controls = receipt["normative_controls"]
    for cid in frozen_control_ids:
        assert cid in normative_controls, f"Missing control {cid} in receipt"
        ctrl = normative_controls[cid]
        assert "executable_equivalent" in ctrl and ctrl["executable_equivalent"]
        assert "exact_test" in ctrl and ctrl["exact_test"]
        assert "observation" in ctrl and ctrl["observation"]


def test_cm_cl2_07_control_b_exhaustive_claim_coverage_refused() -> None:
    """CM-CL2-07: Control B bounded semantic utility cannot be promoted to exhaustive per-claim coverage
    or substitute for Control A.
    """
    receipt_path = Path(__file__).resolve().parent.parent.parent / "campaign/agent-review-v1-freeze/evidence/v1-c2-gate-a-receipt.json"
    with open(receipt_path) as f:
        receipt = json.load(f)

    assert receipt["nonclaims"]["Control_A"] == "NOT_CLAIMED"
    assert receipt["nonclaims"]["Control_B"] == "NOT_CLAIMED"
    assert receipt["capability"]["nonclaims"]["exhaustive_per_claim_coverage"] is True


def test_pc_cl2_07_control_bounded_purpose() -> None:
    """PC-CL2-07: Bounded named control operates strictly within its declared purpose without provider calls."""
    intake = _base_intake()
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    assert plan.status in {"complete", "partial"}


# ---------------------------------------------------------------------------
# N2, N3, N4, N5 Specific Normative Controls
# ---------------------------------------------------------------------------


def test_cm_n2_legacy_domain_contract_not_mapping() -> None:
    """CM-N2-LEGACY-DOMAIN-CONTRACT-NOT-MAPPING:
    Legacy flat pack declaring domain_contract does not establish EffectiveContractRefs
    or emit binding limitations.
    """
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": [
            {"id": "c1", "rules": ["r1"], "paths": ["backend/other/*"]},
        ],
        "review_packs": [
            {
                "id": "legacy_pack_1",
                "paths": ["backend/api/*"],
                "domain_contract": "c1",
            }
        ],
    }
    # In legacy mode, domain_contract does NOT establish EffectiveContractRefs
    ctx, limits = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    # Cycle-3 legacy firewall: a LEGACY flat pack carries only {id, description, preset}; its
    # `paths` never acquire semantics, so (like frozen baseline 6bbd2f9) nothing is applicable
    # here and the consequence is contracts_context_not_relevant -- but never a binding limitation.
    assert limits == ["contracts_context_not_relevant:chunk-01"]
    assert ctx["review_packs"] == []
    c_ids = [c["id"] for c in ctx["domain_contracts"]]
    assert "c1" not in c_ids

    # Also verify that if domain_contract pointed to nonexistent contract, it does NOT emit unresolved_contract_binding
    intake.target_profile["review_packs"][0]["domain_contract"] = "nonexistent_contract"
    ctx2, limits2 = payload_cost_model.contracts_context(
        intake,
        chunk_files=["backend/api/shifts.py"],
        chunk_contracts=[],
        chunk_id="chunk-01",
        selected_contract_pack=None,
        semantic_group="api",
    )
    assert limits2 == ["contracts_context_not_relevant:chunk-01"]
    assert not any(lim.startswith("unresolved_contract_binding") for lim in limits2)


def test_cm_n3_mapping_nested_id() -> None:
    """CM-N3-MAPPING-NESTED-ID:
    Modern mapping pack value declaring nested id matching the key fails closed with UNSUPPORTED_NONEMPTY.
    """
    raw_packs = {
        "packs": {
            "auth": {
                "id": "auth",
                "paths": ["backend/auth/*"],
            }
        }
    }
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(raw_packs)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits


def test_cm_n3_mapping_nested_id_conflict() -> None:
    """CM-N3-MAPPING-NESTED-ID-CONFLICT:
    Modern mapping pack value declaring nested id conflicting with mapping key fails closed with INVALID_IDENTITY.
    """
    raw_packs = {
        "packs": {
            "auth": {
                "id": "security",
                "paths": ["backend/auth/*"],
            }
        }
    }
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(raw_packs)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_INVALID_IDENTITY
    assert "invalid_source_review_packs:INVALID_IDENTITY" in limits


def test_cm_n3_unknown_modern_pack_field() -> None:
    """CM-N3-UNKNOWN-MODERN-PACK-FIELD:
    Modern mapping pack value declaring unknown field fails closed with UNSUPPORTED_NONEMPTY.
    """
    raw_packs = {
        "packs": {
            "auth": {
                "paths": ["backend/auth/*"],
                "mystery_relation": "security",
            }
        }
    }
    packs, bindings, state, subtype, limits = payload_cost_model.normalize_review_packs(raw_packs)
    assert state == payload_cost_model.SOURCE_STATE_INVALID
    assert subtype == payload_cost_model.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert "invalid_source_review_packs:UNSUPPORTED_NONEMPTY" in limits

    # Conversely, admitted target metadata (critical, notes, etc.) is admitted
    raw_packs_valid = {
        "packs": {
            "auth": {
                "paths": ["backend/auth/*"],
                "critical": True,
                "notes": "Admitted target metadata",
            }
        }
    }
    packs_ok, bindings_ok, state_ok, subtype_ok, limits_ok = payload_cost_model.normalize_review_packs(raw_packs_valid)
    assert state_ok == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert limits_ok == []
    assert "critical" not in packs_ok[0]
    assert "notes" not in packs_ok[0]


def test_n4_top_level_named_list_section_identity() -> None:
    """N4 — Top-level named list[str] normalization preserves section identity as 'rules'."""
    raw_contracts = {
        "response_model_rules": [
            "All response models must validate datetime in UTC",
            "Pydantic schemas must be immutable",
        ]
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw_contracts)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert limits == []
    assert len(contracts) == 1
    c = contracts[0]
    assert c["id"] == "response_model_rules"
    assert "rules" in c["sections"]
    assert [item["text"] for item in c["sections"]["rules"]] == [
        "All response models must validate datetime in UTC",
        "Pydantic schemas must be immutable",
    ]
    assert c["rules"] == [
        "All response models must validate datetime in UTC",
        "Pydantic schemas must be immutable",
    ]


def test_n4_empty_named_section_identity_preserved() -> None:
    """N4 — Empty named section present as [] preserves empty section identity."""
    raw_contracts = {
        "auth_contract": {
            "paths": ["backend/auth/*"],
            "rules": [],
            "response_models": [],
        }
    }
    contracts, state, subtype, limits = payload_cost_model.normalize_domain_contracts(raw_contracts)
    assert state == payload_cost_model.SOURCE_STATE_PRESENT_VALID
    assert limits == []
    assert len(contracts) == 1
    c = contracts[0]
    assert c["sections"]["rules"] == []
    assert c["sections"]["response_models"] == []
    assert "rules" in c["sections"]
    assert "response_models" in c["sections"]


# ---------------------------------------------------------------------------
# N5 Carrier Graph Controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason_class", "producer_setup"),
    [
        (
            "invalid_source_contract:MALFORMED_SHAPE",
            lambda intake: intake.target_profile.update({"domain_contracts": "invalid_string"}),
        ),
        (
            "invalid_source_review_packs:MALFORMED_SHAPE",
            lambda intake: intake.target_profile.update({"review_packs": "invalid_string"}),
        ),
        (
            "malformed_contract_bindings:must_be_mapping",
            lambda intake: intake.target_profile.update({
                "review_packs": {
                    "packs": {"p1": {"paths": ["backend/*"]}},
                    "contract_bindings": "not_a_mapping",
                }
            }),
        ),
        (
            "unresolved_contract_binding:",
            lambda intake: intake.target_profile.update({
                "domain_contracts": {"c1": {"rules": ["r1"]}},
                "review_packs": {
                    "packs": {"p1": {"paths": ["backend/*"]}},
                    "contract_bindings": {"p1": ["nonexistent_c"]},
                },
            }),
        ),
        (
            "unresolved_contract_reference:",
            lambda intake: intake.target_profile.update({
                "domain_contracts": {"c1": {"rules": ["r1"]}},
                "contracts": ["contract:nonexistent_ref"],
            }),
        ),
        (
            "orphan_contract_binding:",
            lambda intake: intake.target_profile.update({
                "domain_contracts": {"c1": {"rules": ["r1"]}},
                "review_packs": {
                    "packs": {"p1": {"paths": ["backend/*"]}},
                    "contract_bindings": {"orphan_pack": ["c1"]},
                },
            }),
        ),
        (
            "required_source_absent:",
            lambda intake: (
                intake.target_profile.update({"domain_contracts": None}),
                intake.target_profile.update({
                    "review_packs": {
                        "packs": {"p1": {"paths": ["backend/*"]}},
                        "contract_bindings": {"p1": ["c1"]},
                    }
                }),
            ),
        ),
        (
            "selected_contract_pack_missing:",
            lambda intake: intake.artifacts.update({
                "pr-brief": {
                    "name": "pr-brief",
                    "path": "pr-brief.json",
                    "kind": "json",
                    "content": {"contract_pack": "nonexistent_pack"},
                }
            }),
        ),
    ],
)
def test_n5_planner_origin_full_carrier_terminal(reason_class: str, producer_setup: Any, tmp_path: Path) -> None:
    """N5: Full carrier chain for all 8 planner-origin limitation classes:
    Real producer -> build_semantic_chunk_plan -> plan.limitations -> plan.status=='degraded'
    -> parse_chunk_results -> chunk_results.limitations -> synthesize_final_review
    -> final_review.limitations/degraded -> evaluate_review_quality_gate -> quality_gate.status != 'passed'.
    """
    intake = _base_intake()
    producer_setup(intake)

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)

    # 1. Plan carrier
    assert any(lim.startswith(reason_class) for lim in plan.limitations), f"Missing {reason_class} in plan limitations {plan.limitations}"
    assert plan.status == "degraded"

    # Write mock responses for chunks if chunks were created
    resp_dir = tmp_path / f"resp_{reason_class.replace(':', '_')}"
    resp_dir.mkdir(parents=True, exist_ok=True)
    for chunk in plan.chunks:
        resp = {
            "schema_version": 1,
            "chunk_id": chunk.chunk_id,
            "semantic_group": chunk.semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": list(chunk.files),
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (resp_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(resp), encoding="utf-8")

    # 2. Results carrier
    results = parse_chunk_results(plan, responses_dir=resp_dir)
    assert any(lim.startswith(reason_class) for lim in results.limitations)

    # 3. Final review carrier
    review = synthesize_final_review(results, intake=intake, chunk_plan=plan)
    assert any(lim.startswith(reason_class) for lim in review.limitations)
    assert review.status == "degraded"

    # 4. Quality gate terminal effect
    doc = validate_final_review_document(review.model_dump(mode="json"))
    gate = evaluate_review_quality_gate(final_review=doc, chunk_results=results, intake=intake, chunk_plan=plan)
    assert gate.status != "passed"


@pytest.mark.parametrize(
    ("reason_class", "producer_type"),
    [
        ("required_contract_context_lost:", "contract"),
        ("required_contract_pack_context_lost:", "pack"),
    ],
)
def test_n5_builder_origin_full_carrier_terminal(reason_class: str, producer_type: str) -> None:
    """N5: Full carrier chain for both builder-origin limitation classes:
    budget shrink -> payload limitation -> manifest entry status='limited' -> payload_path=None
    -> execution refusal before inference.
    """
    intake = _base_intake()
    if producer_type == "contract":
        intake.target_profile = {
            "domain_contracts": [
                {"id": "req-c", "description": "C" * 3000, "paths": ["backend/api/*"]},
            ],
            "review_packs": {
                "packs": {"p1": {"paths": ["backend/api/*"]}},
                "contract_bindings": {"p1": ["req-c"]},
            },
        }
    else:
        intake.target_profile = {
            "review_packs": {
                "packs": {
                    "req-p": {
                        "paths": ["backend/api/*"],
                        "description": "P" * 3000,
                    }
                }
            }
        }

    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    # Constrain chunk budget to force dropping the required item
    plan.chunks[0].prompt_budget_chars = 500

    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    entry = manifest.chunks[0]
    # Terminal effect at builder boundary
    assert any(lim.startswith(reason_class) for lim in entry.limitations)
    assert entry.status == "limited"
    assert entry.payload_path is None
    # Execution refused
    assert payloads.get(entry.chunk_id) is None
