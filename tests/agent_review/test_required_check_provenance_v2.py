"""`RequiredCheckProvenanceV2` contract and digests (`#201-C0`, C0-2).

These tests hold the line that `#217` describes: a `RequiredCheckResultV2` is
provenance-agnostic, so the binding has to live somewhere, be exact, and be
impossible to forge by naming a check `pytest`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RequiredCheckConclusionV2, RequiredCheckResultV2
from app.agent_review.required_check_provenance_v2 import (
    ALL_PROVENANCE_REASON_CODES_V2,
    AuthorityEffectV2,
    RequiredCheckProvenanceV2,
    RequiredCheckSourceKindV2,
    SemanticClassV2,
    build_required_check_provenance_v2,
    compute_provenance_digest_v2,
    compute_required_check_digest_v2,
)
from app.agent_review.schema_export_v2 import render_v2_json_schemas

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "agent-review" / "v2"

HEAD_SHA = "a" * 40
BASE_SHA = "c" * 40
MERGE_SHA = "d" * 40


def _result(check_name: str = "pytest", conclusion: RequiredCheckConclusionV2 = RequiredCheckConclusionV2.SUCCESS):
    return RequiredCheckResultV2(
        check_name=check_name,
        required=True,
        deterministic=True,
        conclusion=conclusion,
        head_sha=HEAD_SHA,
    )


def _ci_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "schema_id": "agent-review.required-check-provenance.v2",
        "schema_version": 2,
        "source": "aiops-review-check-provenance",
        "check_name": "pytest",
        "required_check_digest": compute_required_check_digest_v2(_result()),
        "source_kind": RequiredCheckSourceKindV2.AUTHORITATIVE_CI,
        "semantic_class": SemanticClassV2.AUTHORITATIVE,
        "authority_effect": AuthorityEffectV2.PROMOTABLE,
        "authority_transfer": False,
        "repository": "mglpsw/AgentEscala",
        "run_id": "b" * 64,
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "tested_merge_sha": MERGE_SHA,
        "event_type": "pull_request",
        "event_action": "synchronize",
        "verifier_identity": "github-actions",
        "toolchain_digest": "e" * 64,
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_ref": "refs/heads/master",
        "job_name": "Validate repository",
        "ci_run_id": "12345",
        "ci_run_attempt": 1,
        "observed_status": "completed",
        "observed_conclusion": "success",
        "observation_digest": "f" * 64,
        "policy_source_bytes_digest": "0" * 64,
        "policy_source_semantic_digest": "1" * 64,
    }
    fields.update(overrides)
    return fields


def _host_fields(**overrides: object) -> dict[str, object]:
    fields = _ci_fields()
    fields.update(
        {
            "check_name": "ruff",
            "required_check_digest": compute_required_check_digest_v2(_result("ruff")),
            "source_kind": RequiredCheckSourceKindV2.TRUSTED_HOST_PROMOTION,
            "workflow_path": None,
            "workflow_ref": None,
            "job_name": None,
            "ci_run_id": None,
            "ci_run_attempt": None,
            "observed_status": None,
            "observed_conclusion": None,
        }
    )
    fields.update(overrides)
    return fields


# -- the join key -------------------------------------------------------------


def test_check_digest_is_deterministic() -> None:
    assert compute_required_check_digest_v2(_result()) == compute_required_check_digest_v2(_result())


def test_check_digest_distinguishes_conclusion() -> None:
    """The whole point of binding by digest rather than by name: a red result
    and a green result with the same name are different objects."""

    green = compute_required_check_digest_v2(_result(conclusion=RequiredCheckConclusionV2.SUCCESS))
    red = compute_required_check_digest_v2(_result(conclusion=RequiredCheckConclusionV2.FAILURE))
    assert green != red


def test_check_digest_distinguishes_check_name_and_head() -> None:
    assert compute_required_check_digest_v2(_result()) != compute_required_check_digest_v2(_result("mypy"))
    other_head = RequiredCheckResultV2(
        check_name="pytest",
        required=True,
        deterministic=True,
        conclusion=RequiredCheckConclusionV2.SUCCESS,
        head_sha="9" * 40,
    )
    assert compute_required_check_digest_v2(_result()) != compute_required_check_digest_v2(other_head)


def test_check_digest_is_bare_hex_not_caem_prefixed() -> None:
    digest = compute_required_check_digest_v2(_result())
    assert not digest.startswith("sha256:")
    assert len(digest) == 64


# -- construction and self-digest ---------------------------------------------


def test_builder_produces_a_valid_record() -> None:
    record = build_required_check_provenance_v2(**_ci_fields())
    assert record.provenance_digest == compute_provenance_digest_v2(record)


def test_builder_refuses_a_supplied_digest() -> None:
    with pytest.raises(TypeError, match="computed, never supplied"):
        build_required_check_provenance_v2(**_ci_fields(), provenance_digest="0" * 64)


def test_tampered_sidecar_is_refused(  # C0-T14
) -> None:
    record = build_required_check_provenance_v2(**_ci_fields())
    tampered = record.model_dump(mode="json")
    tampered["observed_conclusion"] = "failure"
    with pytest.raises(ValidationError):
        RequiredCheckProvenanceV2.model_validate(tampered)


def test_wrong_self_digest_is_refused() -> None:
    with pytest.raises(ValidationError):
        RequiredCheckProvenanceV2(**_ci_fields(), provenance_digest="0" * 64)


# -- source discipline --------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["workflow_path", "workflow_ref", "job_name", "ci_run_id", "ci_run_attempt", "observed_status", "observed_conclusion"],
)
def test_authoritative_ci_requires_the_complete_identity(missing: str) -> None:
    """Partial CI identity is worse than none -- it reads as verified while
    leaving the spoofable half unchecked."""

    with pytest.raises(ValidationError, match="complete CI identity"):
        build_required_check_provenance_v2(**_ci_fields(**{missing: None}))


@pytest.mark.parametrize(
    "present,value",
    [
        ("workflow_path", ".github/workflows/ci.yml"),
        ("ci_run_id", "12345"),
        ("observed_conclusion", "success"),
    ],
)
def test_host_promotion_must_not_carry_ci_identity(present: str, value: object) -> None:  # C0-T22
    """Dressing a subject-code observation in CI clothing is the exact attack
    `#201-B3`'s class boundary exists to prevent."""

    with pytest.raises(ValidationError, match="must not carry CI identity"):
        build_required_check_provenance_v2(**_host_fields(**{present: value}))


def test_host_promotion_record_is_valid_without_ci_fields() -> None:
    record = build_required_check_provenance_v2(**_host_fields())
    assert record.source_kind is RequiredCheckSourceKindV2.TRUSTED_HOST_PROMOTION
    assert record.ci_run_id is None


def test_advisory_can_never_be_promotable() -> None:
    with pytest.raises(ValidationError, match="never be promotable"):
        build_required_check_provenance_v2(
            **_host_fields(
                semantic_class=SemanticClassV2.ADVISORY,
                authority_effect=AuthorityEffectV2.PROMOTABLE,
            )
        )


def test_authority_transfer_can_only_be_false() -> None:
    """CAEM's own invariant. No provenance record confers its authority on
    anything downstream."""

    with pytest.raises(ValidationError):
        build_required_check_provenance_v2(**_ci_fields(authority_transfer=True))


def test_authoritative_ci_cannot_be_semantically_advisory() -> None:
    with pytest.raises(ValidationError, match="semantically authoritative"):
        build_required_check_provenance_v2(**_ci_fields(semantic_class=SemanticClassV2.ADVISORY))


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        RequiredCheckProvenanceV2.model_validate({**_ci_fields(), "provenance_digest": "0" * 64, "extra": 1})


# -- reason codes -------------------------------------------------------------


def test_reason_codes_are_unique_and_share_one_prefix() -> None:
    assert len(set(ALL_PROVENANCE_REASON_CODES_V2)) == len(ALL_PROVENANCE_REASON_CODES_V2)
    assert all(code.startswith("required_check_provenance_") for code in ALL_PROVENANCE_REASON_CODES_V2)


def test_reason_codes_carry_no_free_text() -> None:
    assert all(code == code.strip().lower() and " " not in code for code in ALL_PROVENANCE_REASON_CODES_V2)


# -- schema export is additive ------------------------------------------------


def test_new_schema_is_exported_and_written_to_disk() -> None:
    schemas = render_v2_json_schemas()
    assert "agent-review.required-check-provenance.v2.schema.json" in schemas
    assert (SCHEMA_DIR / "agent-review.required-check-provenance.v2.schema.json").is_file()


def test_frozen_required_check_result_schema_is_untouched() -> None:
    """`RequiredCheckResultV2` lives inside the published readiness schema. If
    C0 ever grows a field on it, this fires."""

    readiness = json.loads((SCHEMA_DIR / "agent-review.review-readiness.v2.schema.json").read_text(encoding="utf-8"))
    required_check = readiness["$defs"]["RequiredCheckResultV2"]
    assert set(required_check["properties"]) == {
        "check_name",
        "required",
        "deterministic",
        "conclusion",
        "head_sha",
    }


def test_schema_export_is_reproducible() -> None:
    assert render_v2_json_schemas() == render_v2_json_schemas()
