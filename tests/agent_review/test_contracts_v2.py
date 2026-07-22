from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    AgentReviewRunV2,
    ChunkPayloadV2,
    FindingDispositionV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    ResponseBindingError,
    ResponseBindingV2,
    ReviewReadinessV2,
    RunIdentityV2,
    TargetProfileV2,
    canonical_run_identity_bytes,
    compute_run_id,
    validate_chunk_response_envelope_v2,
    validate_response_binding_v2,
)
from app.agent_review.schema_export_v2 import render_v2_json_schemas


FIXTURES = Path(__file__).parent / "fixtures" / "v2"
SCHEMAS = Path(__file__).parents[2] / "schemas" / "agent-review" / "v2"


def _identity() -> dict[str, object]:
    return {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 80,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "evidence_hash": "c" * 64,
    }


def _coverage() -> dict[str, object]:
    return {
        "status": "complete",
        "expected_files": ["app/service.py"],
        "reviewed_files": ["app/service.py"],
        "partially_reviewed_files": [],
        "missing_files": [],
        "must_review_files": ["app/service.py"],
        "missing_must_review_files": [],
    }


def _run() -> dict[str, object]:
    identity = _identity()
    return {
        "schema_id": "agent-review.run.v2",
        "schema_version": 2,
        "source": "aiops-review-run",
        "run_id": "fc85ba5350895387611905ec6e88c957af79cfa1893221d1bccdfe214ac591be",
        "identity": identity,
        "origin": {
            "event_type": "pull_request",
            "event_action": "synchronize",
            "delivery_id": "delivery-80",
        },
        "created_at": "2026-07-22T12:00:00Z",
        "expires_at": "2026-07-23T12:00:00Z",
    }


def _payload() -> dict[str, object]:
    return {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": "fc85ba5350895387611905ec6e88c957af79cfa1893221d1bccdfe214ac591be",
        "identity": _identity(),
        "chunk_id": "api-schema-001",
        "semantic_group": "api_schema_contract",
        "payload_sha256": "d" * 64,
        "coverage": _coverage(),
        "artifact_references": [
            {
                "artifact_id": "full-diff",
                "kind": "diff",
                "sha256": "e" * 64,
                "role": "primary",
            }
        ],
        "contract_references": [
            {
                "contract_id": "contract.api",
                "contract_version": "1",
                "sha256": "f" * 64,
                "scope": "chunk",
                "paths": ["app/service.py"],
            }
        ],
    }


def _success_envelope() -> dict[str, object]:
    return {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": "fc85ba5350895387611905ec6e88c957af79cfa1893221d1bccdfe214ac591be",
        "chunk_id": "api-schema-001",
        "payload_sha256": "d" * 64,
        "head_sha": "2" * 40,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": "req-80-1",
        "finish_reason": "stop",
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "review-complete",
            "findings": [
                {
                    "finding_id": "finding-001",
                    "severity": "P2",
                    "title": "counter-overlap",
                    "file_path": "app/service.py",
                    "line_start": 10,
                    "line_end": 12,
                    "evidence": "non-disjoint-counters",
                    "impact": "double-counting",
                    "confidence": "high",
                    "contract_ids": ["contract.api"],
                    "disposition": "new",
                }
            ],
            "coverage": _coverage(),
            "limitations": [],
        },
    }


def _error_envelope() -> dict[str, object]:
    payload = _success_envelope()
    payload["status"] = "error"
    payload["finish_reason"] = "error"
    payload.pop("result")
    payload["error"] = {"reason_code": "transport_failure", "retryable": True}
    return payload


def _target_profile() -> dict[str, object]:
    return {
        "schema_id": "agent-review.target-profile.v2",
        "schema_version": 2,
        "source": "repo-profile",
        "identity": {
            "repo": "mglpsw/aiops-orchestrator",
            "default_branch": "master",
        },
        "artifacts": [
            {
                "artifact_id": "full-diff",
                "path": "artifacts/full.diff",
                "kind": "diff",
                "required": True,
                "max_bytes": 1000000,
            }
        ],
        "budgets": {
            "max_chunks": 32,
            "total_prompt_chars": 250000,
            "max_chars_per_chunk": 24000,
            "max_files_per_chunk": 50,
            "max_contracts_per_chunk": 50,
        },
        "must_review": {
            "paths": ["app/service.py"],
            "patterns": ["app/**/*.py"],
            "artifact_ids": ["full-diff"],
            "minimum_coverage": "complete",
        },
        "policies": {
            "network_policy": "forbidden",
            "fail_closed": True,
            "redaction_required": True,
            "allow_partial_coverage": False,
            "required_checks": ["pytest"],
            "allowed_semantic_groups": ["api_schema_contract", "tests"],
            "coverage_failure_state": "blocked_pipeline",
            "model_uncertainty_state": "manual_required",
        },
        "contracts": [
            {
                "contract_id": "contract.api",
                "contract_version": "1",
                "path": ".aiops/domain-contracts.yaml",
                "sha256": "f" * 64,
                "scope": "repository",
                "required": True,
            }
        ],
        "limitations": [],
    }


def _readiness() -> dict[str, object]:
    return {
        "schema_id": "agent-review.review-readiness.v2",
        "schema_version": 2,
        "source": "aiops-review-quality-gate",
        "run_id": "fc85ba5350895387611905ec6e88c957af79cfa1893221d1bccdfe214ac591be",
        "head_sha": "2" * 40,
        "evaluated_head_sha": "2" * 40,
        "state": "ready",
        "reason_codes": [],
        "blockers": [],
        "findings": [],
    }


def _validate_json(model: type, payload: dict[str, object]):  # noqa: ANN202
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_golden_run_identity_bytes_and_run_id_are_exact() -> None:
    golden = json.loads((FIXTURES / "golden_run_identity.json").read_text(encoding="utf-8"))
    identity = _validate_json(RunIdentityV2, golden["identity"])

    canonical = canonical_run_identity_bytes(identity)

    assert canonical == golden["canonical_json"].encode("utf-8")
    assert compute_run_id(identity) == golden["run_id"]
    assert AgentReviewRunV2.model_validate_json(json.dumps(_run())).run_id == golden["run_id"]


@pytest.mark.parametrize("field", list(_identity()))
def test_each_material_identity_component_changes_run_id(field: str) -> None:
    original = _identity()
    changed = copy.deepcopy(original)
    if field == "repo":
        changed[field] = "mglpsw/aiops-orchestrator-v2"
    elif field == "pr_number":
        changed[field] = 81
    else:
        value = str(changed[field])
        changed[field] = ("0" if value[0] != "0" else "1") + value[1:]

    first = _validate_json(RunIdentityV2, original)
    second = _validate_json(RunIdentityV2, changed)
    assert compute_run_id(first) != compute_run_id(second)


def test_canonical_identity_is_order_independent_and_not_delimiter_based() -> None:
    identity = _identity()
    reverse_order = dict(reversed(list(identity.items())))

    first = _validate_json(RunIdentityV2, identity)
    second = _validate_json(RunIdentityV2, reverse_order)

    assert canonical_run_identity_bytes(first) == canonical_run_identity_bytes(second)
    decoded = json.loads(canonical_run_identity_bytes(first))
    assert decoded == identity
    assert list(decoded) == sorted(identity)


def test_run_timestamp_is_explicit_and_excluded_from_identity() -> None:
    first = _run()
    second = _run()
    second["created_at"] = "2026-07-22T13:00:00Z"
    second["expires_at"] = None

    first_run = _validate_json(AgentReviewRunV2, first)
    second_run = _validate_json(AgentReviewRunV2, second)

    assert first_run.run_id == second_run.run_id
    canonical = canonical_run_identity_bytes(first_run.identity)
    assert b"created_at" not in canonical
    assert b"expires_at" not in canonical
    assert b"/tmp/" not in canonical
    assert b"Bearer " not in canonical
    assert b"token=" not in canonical


@pytest.mark.parametrize(
    ("model", "payload", "missing"),
    [
        (AgentReviewRunV2, _run, "identity"),
        (ChunkPayloadV2, _payload, "coverage"),
        (TargetProfileV2, _target_profile, "policies"),
        (ReviewReadinessV2, _readiness, "state"),
    ],
)
def test_contracts_reject_missing_fields(model: type, payload, missing: str) -> None:  # noqa: ANN001
    raw = payload()
    raw.pop(missing)
    with pytest.raises(ValidationError):
        _validate_json(model, raw)


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (AgentReviewRunV2, _run),
        (ChunkPayloadV2, _payload),
        (TargetProfileV2, _target_profile),
        (ReviewReadinessV2, _readiness),
    ],
)
def test_contracts_reject_unknown_fields_at_top_level(model: type, payload) -> None:  # noqa: ANN001
    raw = payload()
    raw["unknown"] = "rejected"
    with pytest.raises(ValidationError):
        _validate_json(model, raw)


def test_nested_contract_models_reject_unknown_fields() -> None:
    payload = _target_profile()
    payload["budgets"]["escape_hatch"] = {}  # type: ignore[index]
    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, payload)


@pytest.mark.parametrize(
    ("payload", "mutate"),
    [
        (_run, lambda value: value["identity"].__setitem__("head_sha", "A" * 40)),
        (_run, lambda value: value["identity"].__setitem__("policy_hash", "a" * 63)),
        (_payload, lambda value: value.__setitem__("payload_sha256", "d" * 63)),
        (_target_profile, lambda value: value["contracts"][0].__setitem__("sha256", "F" * 64)),
        (_readiness, lambda value: value.__setitem__("head_sha", "2" * 39)),
    ],
)
def test_invalid_git_shas_and_sha256_hashes_are_rejected(payload, mutate) -> None:  # noqa: ANN001
    raw = payload()
    mutate(raw)
    model = {
        _run: AgentReviewRunV2,
        _payload: ChunkPayloadV2,
        _target_profile: TargetProfileV2,
        _readiness: ReviewReadinessV2,
    }[payload]
    with pytest.raises(ValidationError):
        _validate_json(model, raw)


def test_coercive_types_are_rejected() -> None:
    run = _run()
    run["identity"]["pr_number"] = "80"  # type: ignore[index]
    with pytest.raises(ValidationError):
        _validate_json(AgentReviewRunV2, run)

    response = _success_envelope()
    response["attempt"] = "1"
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(response)


def test_valid_enum_strings_from_json_objects_are_not_treated_as_type_coercion() -> None:
    assert ChunkPayloadV2.model_validate(_payload()).semantic_group.value == "api_schema_contract"
    assert TargetProfileV2.model_validate(_target_profile()).policies.allowed_semantic_groups[0].value == (
        "api_schema_contract"
    )
    assert ReviewReadinessV2.model_validate(_readiness()).state is ReadinessStateV2.READY


def test_run_and_payload_reject_a_run_id_that_does_not_match_identity() -> None:
    for model, payload in ((AgentReviewRunV2, _run()), (ChunkPayloadV2, _payload())):
        payload["run_id"] = "0" * 64
        with pytest.raises(ValidationError):
            _validate_json(model, payload)


def test_success_and_error_envelopes_are_discriminated_and_strict() -> None:
    success = validate_chunk_response_envelope_v2(_success_envelope())
    error = validate_chunk_response_envelope_v2(_error_envelope())
    assert success.status == "success"
    assert error.status == "error"

    hybrid_success = _success_envelope()
    hybrid_success["error"] = {"reason_code": "transport_failure", "retryable": True}
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(hybrid_success)

    hybrid_error = _error_envelope()
    hybrid_error["result"] = _success_envelope()["result"]
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(hybrid_error)


def test_response_envelope_rejects_missing_unknown_and_constant_fields() -> None:
    missing = _success_envelope()
    missing.pop("provider")
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(missing)

    unknown = _success_envelope()
    unknown["unknown"] = "rejected"
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(unknown)

    wrong_schema = _success_envelope()
    wrong_schema["schema_version"] = 1
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(wrong_schema)


def test_response_envelope_rejects_contradictory_finish_reasons() -> None:
    success = _success_envelope()
    success["finish_reason"] = "error"
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(success)

    error = _error_envelope()
    error["finish_reason"] = "stop"
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(error)


def test_error_envelope_cannot_carry_raw_or_sensitive_fields() -> None:
    for forbidden in ("prompt", "raw_response", "token", "headers", "local_path", "clinical_data"):
        payload = _error_envelope()
        payload["error"][forbidden] = "forbidden"  # type: ignore[index]
        with pytest.raises(ValidationError):
            validate_chunk_response_envelope_v2(payload)


@pytest.mark.parametrize(
    ("field", "changed", "reason"),
    [
        ("run_id", "0" * 64, "run_id_mismatch"),
        ("chunk_id", "api-schema-002", "chunk_id_mismatch"),
        ("payload_sha256", "0" * 64, "payload_sha256_mismatch"),
        ("head_sha", "0" * 40, "head_sha_mismatch"),
    ],
)
def test_response_binding_detects_run_chunk_payload_and_head_divergence(
    field: str, changed: str, reason: str
) -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    expected = ResponseBindingV2(
        run_id=envelope.run_id,
        chunk_id=envelope.chunk_id,
        payload_sha256=envelope.payload_sha256,
        head_sha=envelope.head_sha,
    )
    divergent = expected.model_copy(update={field: changed})

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, divergent)
    assert raised.value.reason_code == reason


def test_target_profile_rejects_absolute_and_parent_paths() -> None:
    for path in ("/tmp/full.diff", "../outside/full.diff", "C:\\temp\\full.diff"):
        payload = _target_profile()
        payload["artifacts"][0]["path"] = path  # type: ignore[index]
        with pytest.raises(ValidationError):
            _validate_json(TargetProfileV2, payload)


def test_contracts_reject_secret_like_values_before_serialization() -> None:
    profile = _target_profile()
    profile["artifacts"][0]["artifact_id"] = "ghp_abcdefghijk"  # type: ignore[index]
    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)

    response = _error_envelope()
    response["request_id"] = "ghp_abcdefghijk"
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(response)


@pytest.mark.parametrize(
    ("state", "reasons", "blockers", "findings"),
    [
        (
            "ready",
            [],
            [{"blocker_id": "b1", "reason_code": "policy_failure", "active": True, "finding_id": None}],
            [],
        ),
        ("blocked_code", [], [], []),
        (
            "blocked_pipeline",
            ["confirmed_code_finding"],
            [
                {
                    "blocker_id": "b1",
                    "reason_code": "confirmed_code_finding",
                    "active": True,
                    "finding_id": "finding-001",
                }
            ],
            [],
        ),
        (
            "manual_required",
            ["schema_failure"],
            [{"blocker_id": "b1", "reason_code": "schema_failure", "active": True, "finding_id": None}],
            [],
        ),
    ],
)
def test_readiness_rejects_contradictory_combinations(
    state: str, reasons: list[str], blockers: list[dict[str, object]], findings: list[dict[str, object]]
) -> None:
    payload = _readiness()
    payload.update(state=state, reason_codes=reasons, blockers=blockers, findings=findings)
    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_blocked_code_requires_a_confirmed_actionable_finding() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_code",
        reason_codes=["confirmed_code_finding"],
        blockers=[
            {
                "blocker_id": "b1",
                "reason_code": "confirmed_code_finding",
                "active": True,
                "finding_id": "finding-001",
            }
        ],
        findings=[
            {
                "finding_id": "finding-001",
                "severity": "P2",
                "disposition": "confirmed",
                "actionable": True,
                "justification": None,
                "decided_by": None,
                "evidence": [],
                "superseded_by": None,
            }
        ],
    )
    readiness = _validate_json(ReviewReadinessV2, payload)
    assert readiness.state is ReadinessStateV2.BLOCKED_CODE
    assert readiness.reason_codes == [ReadinessReasonV2.CONFIRMED_CODE_FINDING]
    assert readiness.findings[0].disposition is FindingDispositionV2.CONFIRMED


def test_stale_is_the_only_state_that_accepts_a_different_evaluated_head() -> None:
    stale = _readiness()
    stale["state"] = "stale"
    stale["evaluated_head_sha"] = "3" * 40
    assert _validate_json(ReviewReadinessV2, stale).state is ReadinessStateV2.STALE

    not_stale = _readiness()
    not_stale["evaluated_head_sha"] = "3" * 40
    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, not_stale)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "unknown"),
        ("reason_codes", ["unknown"]),
    ],
)
def test_readiness_rejects_unknown_state_and_reason(field: str, value: object) -> None:
    payload = _readiness()
    payload[field] = value
    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_readiness_rejects_unknown_finding_disposition() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-001",
            "severity": "P2",
            "disposition": "unknown",
            "actionable": False,
            "justification": None,
            "decided_by": None,
            "evidence": [],
            "superseded_by": None,
        }
    ]
    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_dismissed_finding_requires_owner_justification_and_evidence() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-001",
            "severity": "P2",
            "disposition": "dismissed",
            "actionable": False,
            "justification": None,
            "decided_by": None,
            "evidence": [],
            "superseded_by": None,
        }
    ]
    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_exported_json_schemas_are_stable_and_deny_unknown_objects() -> None:
    rendered = render_v2_json_schemas()
    assert set(rendered) == {
        "agent-review.run.v2.schema.json",
        "agent-review.chunk-payload.v2.schema.json",
        "agent-review.chunk-response-envelope.v2.schema.json",
        "agent-review.target-profile.v2.schema.json",
        "agent-review.review-readiness.v2.schema.json",
    }

    for filename, schema in rendered.items():
        committed = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
        assert committed == schema
        _assert_objects_forbid_additional_properties(schema)


def _assert_objects_forbid_additional_properties(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        for child in value.values():
            _assert_objects_forbid_additional_properties(child)
    elif isinstance(value, list):
        for child in value:
            _assert_objects_forbid_additional_properties(child)
