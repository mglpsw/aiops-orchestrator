from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
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
    canonical_chunk_payload_bytes_v2,
    canonical_run_identity_bytes,
    compute_manifest_hash_v2,
    compute_payload_sha256_v2,
    compute_response_sha256_v2,
    compute_run_id,
    validate_chunk_response_envelope_v2,
    validate_response_binding_v2,
    verify_payload_sha256_v2,
)
from app.agent_review.schema_export_v2 import render_v2_json_schema_text, render_v2_json_schemas


FIXTURES = Path(__file__).parent / "fixtures" / "v2"
SCHEMAS = Path(__file__).parents[2] / "schemas" / "agent-review" / "v2"
REPO_ROOT = Path(__file__).parents[2]


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
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
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
        "degradation_causes": [],
    }


def _run() -> dict[str, object]:
    identity = _identity()
    return {
        "schema_id": "agent-review.run.v2",
        "schema_version": 2,
        "source": "aiops-review-run",
        "run_id": "7252956d2e854369a7fcade0870be5d7ddea514c629eb566d4f240622c696dba",
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
    payload: dict[str, object] = {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": "7252956d2e854369a7fcade0870be5d7ddea514c629eb566d4f240622c696dba",
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
    payload["payload_sha256"] = compute_payload_sha256_v2(payload)
    return payload


def _success_envelope() -> dict[str, object]:
    envelope: dict[str, object] = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": "7252956d2e854369a7fcade0870be5d7ddea514c629eb566d4f240622c696dba",
        "chunk_id": "api-schema-001",
        "payload_sha256": _payload()["payload_sha256"],
        "head_sha": "2" * 40,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": "req-80-1",
        "finish_reason": "stop",
        "response_received": True,
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
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _error_envelope() -> dict[str, object]:
    payload = _success_envelope()
    payload["status"] = "error"
    payload["finish_reason"] = "error"
    payload.pop("result")
    payload["error"] = {"reason_code": "transport_failure", "retryable": True}
    payload["response_received"] = False
    payload["response_sha256"] = None
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
        "run_id": "7252956d2e854369a7fcade0870be5d7ddea514c629eb566d4f240622c696dba",
        "identity": _identity(),
        "evaluated_run_id": "7252956d2e854369a7fcade0870be5d7ddea514c629eb566d4f240622c696dba",
        "evaluated_identity": _identity(),
        "head_sha": "2" * 40,
        "evaluated_head_sha": "2" * 40,
        "pr_state": "open",
        "checks": [
            {
                "check_name": "Validate repository",
                "required": True,
                "deterministic": True,
                "conclusion": "success",
                "head_sha": "2" * 40,
            }
        ],
        "coverage": _coverage(),
        "pipeline": {"degraded": False, "causes": []},
        "state": "ready",
        "reason_codes": [],
        "blockers": [],
        "findings": [],
    }


def _new_lifecycle_finding(
    finding_id: str = "finding-new",
    severity: str = "P2",
    observed_at_head_sha: str | None = None,
) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "severity": severity,
        "observed_at_head_sha": observed_at_head_sha or "2" * 40,
        "disposition": "new",
        "actionable": True,
        "justification": None,
        "decided_by": None,
        "decided_at_head_sha": None,
        "evidence": [],
        "superseded_by": None,
    }


def _confirmed_lifecycle_finding(
    finding_id: str = "finding-confirmed",
    severity: str = "P2",
) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "severity": severity,
        "observed_at_head_sha": "2" * 40,
        "disposition": "confirmed",
        "actionable": True,
        "justification": None,
        "decided_by": "reviewer-1",
        "decided_at_head_sha": "2" * 40,
        "evidence": [],
        "superseded_by": None,
    }


def _validate_json(model: type, payload: dict[str, object]):  # noqa: ANN202
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def _canonical_without_field(payload: dict[str, object], field: str) -> bytes:
    material = copy.deepcopy(payload)
    material.pop(field, None)
    return json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_without_field(payload: dict[str, object], field: str) -> str:
    return hashlib.sha256(_canonical_without_field(payload, field)).hexdigest()


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
    raw_envelope = _success_envelope()
    raw_envelope[field] = changed
    raw_envelope["response_sha256"] = compute_response_sha256_v2(raw_envelope)
    envelope = validate_chunk_response_envelope_v2(raw_envelope)
    payload = _validate_json(ChunkPayloadV2, _payload())
    expected = ResponseBindingV2(payload=payload)

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, expected)
    assert raised.value.reason_code == reason


def test_response_binding_rejects_summary_changed_after_validation() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    changed_result = envelope.result.model_copy(update={"summary": "review-changed"})
    tampered = envelope.model_copy(update={"result": changed_result})
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(tampered, payload)

    assert raised.value.reason_code == "response_contract_invalid"


def test_response_binding_rejects_finding_changed_after_validation() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    changed_finding = envelope.result.findings[0].model_copy(update={"impact": "changed-impact"})
    changed_result = envelope.result.model_copy(update={"findings": [changed_finding]})
    tampered = envelope.model_copy(update={"result": changed_result})
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(tampered, payload)

    assert raised.value.reason_code == "response_contract_invalid"


def test_response_binding_rejects_model_copy_with_stale_response_hash() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    tampered = envelope.model_copy(update={"request_id": "req-80-2"})
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(tampered, payload)

    assert raised.value.reason_code == "response_contract_invalid"


def test_response_binding_rejects_nested_finding_list_mutation_after_validation() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    envelope.result.findings.append(
        envelope.result.findings[0].model_copy(update={"finding_id": "finding-002"})
    )
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, payload)

    assert raised.value.reason_code == "response_contract_invalid"


def test_response_binding_accepts_legitimate_received_response() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    payload = _validate_json(ChunkPayloadV2, _payload())

    assert validate_response_binding_v2(envelope, payload) is None


def test_response_binding_rejects_coverage_outside_payload_scope() -> None:
    raw_envelope = _success_envelope()
    raw_envelope["result"]["coverage"] = {  # type: ignore[index]
        "status": "complete",
        "expected_files": ["app/outside.py"],
        "reviewed_files": ["app/outside.py"],
        "partially_reviewed_files": [],
        "missing_files": [],
        "must_review_files": [],
        "missing_must_review_files": [],
        "degradation_causes": [],
    }
    raw_envelope["result"]["findings"] = []  # type: ignore[index]
    raw_envelope["response_sha256"] = compute_response_sha256_v2(raw_envelope)
    envelope = validate_chunk_response_envelope_v2(raw_envelope)
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, payload)

    assert raised.value.reason_code == "response_scope_mismatch"


def test_response_binding_rejects_finding_outside_payload_scope() -> None:
    raw_envelope = _success_envelope()
    raw_envelope["result"]["findings"][0]["file_path"] = "app/outside.py"  # type: ignore[index]
    raw_envelope["response_sha256"] = compute_response_sha256_v2(raw_envelope)
    envelope = validate_chunk_response_envelope_v2(raw_envelope)
    payload = _validate_json(ChunkPayloadV2, _payload())

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, payload)

    assert raised.value.reason_code == "response_scope_mismatch"


def test_response_binding_accepts_transport_failure_without_response_hash() -> None:
    envelope = validate_chunk_response_envelope_v2(_error_envelope())
    payload = _validate_json(ChunkPayloadV2, _payload())

    assert envelope.response_received is False
    assert envelope.response_sha256 is None
    assert validate_response_binding_v2(envelope, payload) is None


def test_response_binding_normalizes_a_model_copy_with_stale_payload_hash() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    payload = _validate_json(ChunkPayloadV2, _payload())
    tampered = payload.model_copy(
        update={"semantic_group": payload.semantic_group.__class__.TESTS}
    )

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, tampered)

    assert raised.value.reason_code == "payload_contract_invalid"
    assert raised.value.__cause__ is not None


@pytest.mark.parametrize("mutation", ["coverage", "references"])
def test_response_binding_normalizes_nested_payload_mutations(mutation: str) -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    payload = _validate_json(ChunkPayloadV2, _payload())
    if mutation == "coverage":
        payload.coverage.reviewed_files.append("app/unexpected.py")
    else:
        payload.artifact_references[0] = payload.artifact_references[0].model_copy(
            update={"sha256": "0" * 64}
        )

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, payload)

    assert raised.value.reason_code == "payload_contract_invalid"
    assert raised.value.__cause__ is not None


def test_response_binding_normalizes_a_malformed_expected_payload() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    payload = _validate_json(ChunkPayloadV2, _payload())
    malformed = payload.model_copy(update={"run_id": "malformed"})

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, malformed)

    assert raised.value.reason_code == "payload_contract_invalid"
    assert raised.value.__cause__ is not None


def test_response_binding_keeps_payload_hash_mismatch_for_two_valid_payloads() -> None:
    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    other_payload = _payload()
    other_payload["artifact_references"][0]["sha256"] = "0" * 64  # type: ignore[index]
    other_payload["payload_sha256"] = compute_payload_sha256_v2(other_payload)
    payload = _validate_json(ChunkPayloadV2, other_payload)

    with pytest.raises(ResponseBindingError) as raised:
        validate_response_binding_v2(envelope, payload)

    assert raised.value.reason_code == "payload_sha256_mismatch"
    assert raised.value.__cause__ is None


def test_target_profile_rejects_absolute_and_parent_paths() -> None:
    for path in ("/tmp/full.diff", "../outside/full.diff", "C:\\temp\\full.diff"):
        payload = _target_profile()
        payload["artifacts"][0]["path"] = path  # type: ignore[index]
        with pytest.raises(ValidationError):
            _validate_json(TargetProfileV2, payload)


@pytest.mark.parametrize(
    "branch_name",
    ["master", "develop", "release/2026.07", "feature/agent-review-v2"],
)
def test_target_profile_accepts_valid_git_branch_names(branch_name: str) -> None:
    profile = _target_profile()
    profile["identity"]["default_branch"] = branch_name  # type: ignore[index]

    parsed = _validate_json(TargetProfileV2, profile)

    assert parsed.identity.default_branch == branch_name


@pytest.mark.parametrize(
    "branch_name",
    [
        "feature name",
        "feature\\name",
        "feature..name",
        "feature@{1}",
        "feature//name",
        "/feature",
        "feature/",
        "feature\x01name",
        ".hidden/name",
        "feature/name.lock",
        "feature.",
        "@",
        "-feature",
        "feature~1",
        "feature^1",
        "feature:name",
        "feature?name",
        "feature*name",
        "feature[name",
        "HEAD",
    ],
)
def test_target_profile_rejects_ambiguous_or_unsafe_git_branch_names(
    branch_name: str,
) -> None:
    profile = _target_profile()
    profile["identity"]["default_branch"] = branch_name  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)


def test_target_profile_branch_name_is_strict_and_documented_in_json_schema() -> None:
    profile = _target_profile()
    profile["identity"]["default_branch"] = 123  # type: ignore[index]
    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)

    schema = render_v2_json_schemas()["agent-review.target-profile.v2.schema.json"]
    branch_schema = schema["$defs"]["TargetIdentityV2"]["properties"]["default_branch"]
    assert branch_schema["x-git-ref-format"] == "--branch"
    assert "Git branch name" in branch_schema["description"]
    assert branch_schema["not"]["const"] == "HEAD"


@pytest.mark.parametrize(
    "path",
    ["app/./service.py", "app//service.py", "app/service.py/", ".", "./app/service.py"],
)
def test_relative_paths_reject_non_normalized_posix_aliases(path: str) -> None:
    profile = _target_profile()
    profile["artifacts"][0]["path"] = path  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)


def test_coverage_cannot_duplicate_one_file_through_a_path_alias() -> None:
    payload = _payload()
    payload["coverage"] = {
        "status": "complete",
        "expected_files": ["app/service.py", "app/./service.py"],
        "reviewed_files": ["app/service.py", "app/./service.py"],
        "partially_reviewed_files": [],
        "missing_files": [],
        "must_review_files": ["app/service.py", "app/./service.py"],
        "missing_must_review_files": [],
        "degradation_causes": [],
    }
    with pytest.raises(ValidationError):
        payload["payload_sha256"] = compute_payload_sha256_v2(payload)
        _validate_json(ChunkPayloadV2, payload)


def test_coverage_cannot_partition_one_file_under_two_path_spellings() -> None:
    payload = _payload()
    payload["coverage"] = {
        "status": "partial",
        "expected_files": ["app/service.py", "app/./service.py"],
        "reviewed_files": ["app/service.py"],
        "partially_reviewed_files": [],
        "missing_files": ["app/./service.py"],
        "must_review_files": ["app/service.py", "app/./service.py"],
        "missing_must_review_files": ["app/./service.py"],
        "degradation_causes": [],
    }
    with pytest.raises(ValidationError):
        payload["payload_sha256"] = compute_payload_sha256_v2(payload)
        _validate_json(ChunkPayloadV2, payload)


def test_relative_patterns_preserve_normalized_posix_globs() -> None:
    profile = _target_profile()
    profile["must_review"]["patterns"] = [  # type: ignore[index]
        "app/**/*.py",
        "tests/**/test_*.py",
        "**/*.md",
    ]

    parsed = _validate_json(TargetProfileV2, profile)

    assert parsed.must_review.patterns == ["app/**/*.py", "tests/**/test_*.py", "**/*.md"]


@pytest.mark.parametrize("pattern", ["app/./**/*.py", "app//**/*.py", "app/**/*.py/"])
def test_relative_patterns_reject_non_normalized_aliases(pattern: str) -> None:
    profile = _target_profile()
    profile["must_review"]["patterns"] = [pattern]  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)


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
                "observed_at_head_sha": "2" * 40,
                "disposition": "confirmed",
                "actionable": True,
                "justification": None,
                "decided_by": "reviewer-1",
                "decided_at_head_sha": "2" * 40,
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
    stale["evaluated_identity"]["head_sha"] = "3" * 40  # type: ignore[index]
    stale["evaluated_run_id"] = compute_run_id(_validate_json(RunIdentityV2, stale["evaluated_identity"]))
    stale["checks"][0]["head_sha"] = "3" * 40  # type: ignore[index]
    stale["reason_codes"] = ["head_mismatch"]
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
            "observed_at_head_sha": "2" * 40,
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
            "observed_at_head_sha": "2" * 40,
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


def test_manifest_hash_is_an_independent_material_run_identity_component() -> None:
    identity = _identity()
    identity["manifest_hash"] = "e" * 64

    parsed = _validate_json(RunIdentityV2, identity)
    canonical = canonical_run_identity_bytes(parsed)

    assert json.loads(canonical)["manifest_hash"] == "e" * 64


def test_payload_hash_covers_every_material_field_and_rejects_tampering() -> None:
    payload = _payload()
    payload["payload_sha256"] = _sha256_without_field(payload, "payload_sha256")
    _validate_json(ChunkPayloadV2, payload)

    tampered = copy.deepcopy(payload)
    tampered["artifact_references"][0]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(ValidationError):
        _validate_json(ChunkPayloadV2, tampered)


def test_golden_payload_hash_preimage_and_digest_are_byte_exact() -> None:
    golden = json.loads((FIXTURES / "golden_chunk_payload_hash.json").read_text(encoding="utf-8"))
    payload = _payload()

    assert canonical_chunk_payload_bytes_v2(payload) == golden["canonical_json"].encode("utf-8")
    assert compute_payload_sha256_v2(payload) == golden["payload_sha256"]
    assert payload["payload_sha256"] == golden["payload_sha256"]


def test_payload_hash_is_independent_of_dictionary_insertion_order() -> None:
    payload = _payload()
    reversed_payload = dict(reversed(list(payload.items())))

    assert _sha256_without_field(payload, "payload_sha256") == _sha256_without_field(
        reversed_payload, "payload_sha256"
    )


def test_response_hash_covers_the_sanitized_envelope_material() -> None:
    envelope = _success_envelope()
    envelope["response_sha256"] = _sha256_without_field(envelope, "response_sha256")
    validate_chunk_response_envelope_v2(envelope)

    tampered = copy.deepcopy(envelope)
    tampered["result"]["summary"] = "review-changed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(tampered)


def test_transport_failure_without_response_has_no_response_hash() -> None:
    envelope = _error_envelope()
    envelope["response_received"] = False
    envelope["response_sha256"] = None

    parsed = validate_chunk_response_envelope_v2(envelope)

    assert parsed.response_received is False
    assert parsed.response_sha256 is None


def test_coverage_rejects_a_silently_omitted_expected_file() -> None:
    payload = _payload()
    payload["coverage"] = {
        "status": "partial",
        "expected_files": ["app/service.py", "app/partial.py", "app/omitted.py"],
        "reviewed_files": ["app/service.py"],
        "partially_reviewed_files": ["app/partial.py"],
        "missing_files": [],
        "must_review_files": ["app/service.py"],
        "missing_must_review_files": [],
        "degradation_causes": [],
    }

    with pytest.raises(ValidationError):
        _validate_json(ChunkPayloadV2, payload)


def test_degraded_coverage_requires_a_structured_cause() -> None:
    payload = _payload()
    payload["coverage"]["status"] = "degraded"  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate_json(ChunkPayloadV2, payload)


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_call", "unknown"])
def test_success_rejects_every_non_conclusive_finish_reason(finish_reason: str) -> None:
    envelope = _success_envelope()
    envelope["finish_reason"] = finish_reason

    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(envelope)


def test_readiness_can_represent_all_required_proof_inputs() -> None:
    payload = _readiness()
    payload.update(
        identity=_identity(),
        evaluated_run_id=payload["run_id"],
        pr_state="open",
        checks=[
            {
                "check_name": "Validate repository",
                "required": True,
                "deterministic": True,
                "conclusion": "success",
                "head_sha": payload["head_sha"],
            }
        ],
        coverage=_coverage(),
        pipeline={"degraded": False, "causes": []},
    )

    parsed = _validate_json(ReviewReadinessV2, payload)

    assert parsed.pr_state.value == "open"
    assert parsed.checks[0].conclusion.value == "success"
    assert parsed.coverage.status.value == "complete"
    assert parsed.pipeline.degraded is False


def test_p3_finding_cannot_create_a_code_blocker() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_code",
        reason_codes=["confirmed_code_finding"],
        blockers=[
            {
                "blocker_id": "b1",
                "reason_code": "confirmed_code_finding",
                "active": True,
                "finding_id": "finding-003",
            }
        ],
        findings=[
            {
                "finding_id": "finding-003",
                "severity": "P3",
                "observed_at_head_sha": "2" * 40,
                "disposition": "confirmed",
                "actionable": True,
                "justification": None,
                "decided_by": "reviewer-1",
                "decided_at_head_sha": "2" * 40,
                "evidence": [],
                "superseded_by": None,
            }
        ],
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


@pytest.mark.parametrize(
    ("disposition", "actionable"),
    [
        ("new", False),
        ("confirmed", False),
        ("fixed", True),
        ("dismissed", True),
        ("superseded", True),
        ("stale", True),
    ],
)
def test_finding_lifecycle_actionability_is_coherent(disposition: str, actionable: bool) -> None:
    finding = {
        "finding_id": "finding-001",
        "severity": "P2",
        "observed_at_head_sha": "2" * 40,
        "disposition": disposition,
        "actionable": actionable,
        "justification": "reviewed" if disposition == "dismissed" else None,
        "decided_by": "reviewer-1" if disposition != "new" else None,
        "decided_at_head_sha": "2" * 40 if disposition != "new" else None,
        "evidence": [{"kind": "test", "reference": "pytest", "head_sha": "2" * 40}]
        if disposition in {"fixed", "dismissed"}
        else [],
        "superseded_by": "finding-002" if disposition == "superseded" else None,
    }
    payload = _readiness()
    payload["findings"] = [finding]

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_pipeline_blocker_cannot_point_to_an_arbitrary_finding() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_pipeline",
        reason_codes=["schema_failure"],
        blockers=[
            {
                "blocker_id": "pipeline-1",
                "reason_code": "schema_failure",
                "active": True,
                "finding_id": "finding-ghost",
            }
        ],
        pipeline={
            "degraded": True,
            "causes": [
                {
                    "reason_code": "schema_failure",
                    "component": "schema-export",
                    "detail": "schema validation failed",
                }
            ],
        },
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_blocked_pipeline_preserves_partial_findings_for_audit() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_pipeline",
        reason_codes=["transport_failure"],
        blockers=[
            {
                "blocker_id": "pipeline-1",
                "reason_code": "transport_failure",
                "active": True,
                "finding_id": None,
            }
        ],
        pipeline={
            "degraded": True,
            "causes": [
                {
                    "reason_code": "transport_failure",
                    "component": "provider-transport",
                    "detail": "response unavailable",
                }
            ],
        },
        findings=[
            {
                "finding_id": "finding-partial",
                "severity": "P2",
                "observed_at_head_sha": "2" * 40,
                "disposition": "new",
                "actionable": True,
                "justification": None,
                "decided_by": None,
                "decided_at_head_sha": None,
                "evidence": [],
                "superseded_by": None,
            }
        ],
    )

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.BLOCKED_PIPELINE


@pytest.mark.parametrize(
    ("field", "unsafe"),
    [
        ("fail_closed", False),
        ("redaction_required", False),
        ("allow_partial_coverage", True),
    ],
)
def test_target_profile_cannot_disable_engine_boundaries(field: str, unsafe: bool) -> None:
    profile = _target_profile()
    profile["policies"][field] = unsafe  # type: ignore[index]

    with pytest.raises(ValidationError):
        _validate_json(TargetProfileV2, profile)


def test_safe_text_accepts_pt_br_and_small_technical_evidence() -> None:
    envelope = _success_envelope()
    envelope["result"]["summary"] = "Revisão concluída: ação válida — sem regressão."  # type: ignore[index]
    envelope["result"]["findings"][0]["evidence"] = (  # type: ignore[index]
        'if (count >= 2) { return "ok"; }'
    )
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)

    parsed = validate_chunk_response_envelope_v2(envelope)

    assert parsed.result.summary.startswith("Revisão concluída")


def test_legitimate_check_names_and_secret_scan_identifier_are_allowed() -> None:
    profile = _target_profile()
    profile["policies"]["required_checks"] = ["Validate repository", "secret-scan"]  # type: ignore[index]

    parsed = _validate_json(TargetProfileV2, profile)

    assert parsed.policies.required_checks == ["Validate repository", "secret-scan"]


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "Authorization: Bearer abcdefghijklmnop",
        "/home/runner/work/private/review.json",
        r"C:\\Users\\runner\\private\\review.json",
    ],
)
def test_safe_text_rejects_real_secrets_and_absolute_paths(unsafe_text: str) -> None:
    envelope = _success_envelope()
    envelope["result"]["summary"] = unsafe_text  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(envelope)


def test_manifest_hash_is_canonical_order_independent_and_material() -> None:
    first = {
        "schema_id": "agent-review.chunk-payload-manifest.v2",
        "chunks": [{"chunk_id": "api-schema-001", "payload_sha256": "a" * 64}],
    }
    reordered = dict(reversed(list(first.items())))
    changed = copy.deepcopy(first)
    changed["chunks"][0]["payload_sha256"] = "b" * 64  # type: ignore[index]

    assert compute_manifest_hash_v2(first) == compute_manifest_hash_v2(reordered)
    assert compute_manifest_hash_v2(first) != compute_manifest_hash_v2(changed)


@pytest.mark.parametrize(
    "unsafe_key",
    [
        "/home/runner/.ssh/id_rsa",
        "Authorization: Bearer abcdefghijklmnop",
    ],
)
def test_manifest_hash_rejects_sensitive_nested_object_keys(unsafe_key: str) -> None:
    manifest = {
        "schema_id": "agent-review.chunk-payload-manifest.v2",
        "artifacts": {"nested": {unsafe_key: "evidence"}},
    }

    with pytest.raises(ValueError):
        compute_manifest_hash_v2(manifest)


def test_manifest_hash_accepts_relative_path_object_keys() -> None:
    manifest = {
        "schema_id": "agent-review.chunk-payload-manifest.v2",
        "artifacts": {"app/service.py": "a" * 64},
    }

    assert len(compute_manifest_hash_v2(manifest)) == 64


@pytest.mark.parametrize(
    "material_field",
    ["identity", "chunk_id", "semantic_group", "coverage", "artifact", "contract"],
)
def test_each_payload_material_component_changes_payload_hash(material_field: str) -> None:
    original = _payload()
    changed = copy.deepcopy(original)
    if material_field == "identity":
        changed["identity"]["head_sha"] = "5" * 40  # type: ignore[index]
        identity = _validate_json(RunIdentityV2, changed["identity"])
        changed["run_id"] = compute_run_id(identity)
    elif material_field == "chunk_id":
        changed["chunk_id"] = "api-schema-002"
    elif material_field == "semantic_group":
        changed["semantic_group"] = "tests"
    elif material_field == "coverage":
        coverage = _coverage()
        coverage.update(
            expected_files=["app/other.py"],
            reviewed_files=["app/other.py"],
            must_review_files=["app/other.py"],
        )
        changed["coverage"] = coverage
    elif material_field == "artifact":
        changed["artifact_references"][0]["sha256"] = "0" * 64  # type: ignore[index]
    else:
        changed["contract_references"][0]["sha256"] = "0" * 64  # type: ignore[index]

    assert compute_payload_sha256_v2(original) != compute_payload_sha256_v2(changed)


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_call", "error", "unknown"])
def test_non_conclusive_finish_reasons_are_represented_as_errors(finish_reason: str) -> None:
    envelope = _error_envelope()
    envelope["response_received"] = True
    envelope["finish_reason"] = finish_reason
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)

    assert validate_chunk_response_envelope_v2(envelope).status == "error"


def test_received_error_response_hash_rejects_tampering() -> None:
    envelope = _error_envelope()
    envelope["response_received"] = True
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    validate_chunk_response_envelope_v2(envelope)

    envelope["error"]["retryable"] = False  # type: ignore[index]
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(envelope)


def test_model_copy_cannot_bypass_payload_or_response_hash_verification() -> None:
    payload = _validate_json(ChunkPayloadV2, _payload())
    copied_payload = payload.model_copy(update={"payload_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        verify_payload_sha256_v2(copied_payload)

    envelope = validate_chunk_response_envelope_v2(_success_envelope())
    copied_envelope = envelope.model_copy(update={"response_sha256": "0" * 64})
    with pytest.raises(ValidationError):
        validate_chunk_response_envelope_v2(copied_envelope)


@pytest.mark.parametrize("case", ["overlap", "status", "must_review"])
def test_coverage_rejects_overlap_status_and_must_review_contradictions(case: str) -> None:
    payload = _payload()
    coverage = _coverage()
    if case == "overlap":
        coverage.update(
            status="partial",
            reviewed_files=["app/service.py"],
            partially_reviewed_files=["app/service.py"],
            missing_must_review_files=["app/service.py"],
        )
    elif case == "status":
        coverage["status"] = "partial"
    else:
        coverage["missing_must_review_files"] = ["app/service.py"]
    payload["coverage"] = coverage

    with pytest.raises(ValidationError):
        _validate_json(ChunkPayloadV2, payload)


def test_degraded_coverage_accepts_only_fully_accounted_structured_causes() -> None:
    payload = _payload()
    payload["coverage"] = {
        "status": "degraded",
        "expected_files": ["app/service.py"],
        "reviewed_files": [],
        "partially_reviewed_files": [],
        "missing_files": ["app/service.py"],
        "must_review_files": ["app/service.py"],
        "missing_must_review_files": ["app/service.py"],
        "degradation_causes": [
            {
                "reason_code": "artifact_missing",
                "affected_files": ["app/service.py"],
                "detail": "required diff artifact unavailable",
            }
        ],
    }
    payload["payload_sha256"] = compute_payload_sha256_v2(payload)

    assert _validate_json(ChunkPayloadV2, payload).coverage.status.value == "degraded"


@pytest.mark.parametrize("case", ["closed", "merged", "checks_missing", "check_failed", "coverage", "degraded"])
def test_ready_rejects_missing_or_contradictory_proof(case: str) -> None:
    payload = _readiness()
    if case in {"closed", "merged"}:
        payload["pr_state"] = case
    elif case == "checks_missing":
        payload["checks"] = []
    elif case == "check_failed":
        payload["checks"][0]["conclusion"] = "failure"  # type: ignore[index]
    elif case == "coverage":
        payload["coverage"] = {
            "status": "partial",
            "expected_files": ["app/service.py"],
            "reviewed_files": [],
            "partially_reviewed_files": [],
            "missing_files": ["app/service.py"],
            "must_review_files": ["app/service.py"],
            "missing_must_review_files": ["app/service.py"],
            "degradation_causes": [],
        }
    else:
        payload["pipeline"] = {
            "degraded": True,
            "causes": [
                {
                    "reason_code": "model_uncertainty",
                    "component": "provider-response",
                    "detail": "response was inconclusive",
                }
            ],
        }

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_ready_allows_an_isolated_actionable_p3_without_code_blocking() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-p3",
            "severity": "P3",
            "observed_at_head_sha": "2" * 40,
            "disposition": "new",
            "actionable": True,
            "justification": None,
            "decided_by": None,
            "decided_at_head_sha": None,
            "evidence": [],
            "superseded_by": None,
        }
    ]

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.READY


def test_dismissal_is_typed_owned_justified_and_bound_to_a_head() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-dismissed",
            "severity": "P2",
            "observed_at_head_sha": "2" * 40,
            "disposition": "dismissed",
            "actionable": False,
            "justification": "false positive confirmed by regression test",
            "decided_by": "reviewer-1",
            "decided_at_head_sha": "2" * 40,
            "evidence": [
                {"kind": "test", "reference": "pytest-contracts-v2", "head_sha": "2" * 40}
            ],
            "superseded_by": None,
        }
    ]

    assert _validate_json(ReviewReadinessV2, payload).findings[0].decided_by == "reviewer-1"


def test_ready_rejects_dismissed_p2_decided_on_a_previous_head() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-dismissed-old",
            "severity": "P2",
            "observed_at_head_sha": "2" * 40,
            "disposition": "dismissed",
            "actionable": False,
            "justification": "dismissal must be revalidated",
            "decided_by": "reviewer-1",
            "decided_at_head_sha": "1" * 40,
            "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "2" * 40}],
            "superseded_by": None,
        }
    ]

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


@pytest.mark.parametrize("disposition", ["fixed", "confirmed", "superseded"])
def test_non_new_dispositions_reject_a_decision_from_a_previous_head(disposition: str) -> None:
    payload = _readiness()
    finding = {
        "finding_id": "finding-old-decision",
        "severity": "P2",
        "observed_at_head_sha": "2" * 40,
        "disposition": disposition,
        "actionable": disposition == "confirmed",
        "justification": None,
        "decided_by": "reviewer-1",
        "decided_at_head_sha": "1" * 40,
        "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "2" * 40}]
        if disposition == "fixed"
        else [],
        "superseded_by": "finding-successor" if disposition == "superseded" else None,
    }
    payload["findings"] = [finding]
    if disposition == "confirmed":
        payload.update(
            state="blocked_code",
            reason_codes=["confirmed_code_finding"],
            blockers=[
                {
                    "blocker_id": "code-1",
                    "reason_code": "confirmed_code_finding",
                    "active": True,
                    "finding_id": "finding-old-decision",
                }
            ],
        )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_readiness_rejects_disposition_evidence_from_a_previous_head() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-old-evidence",
            "severity": "P2",
            "observed_at_head_sha": "2" * 40,
            "disposition": "dismissed",
            "actionable": False,
            "justification": "evidence must be revalidated",
            "decided_by": "reviewer-1",
            "decided_at_head_sha": "2" * 40,
            "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "1" * 40}],
            "superseded_by": None,
        }
    ]

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_readiness_rejects_a_finding_observed_on_a_previous_head() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-old-observation",
            "severity": "P3",
            "disposition": "new",
            "actionable": True,
            "observed_at_head_sha": "1" * 40,
            "justification": None,
            "decided_by": None,
            "decided_at_head_sha": None,
            "evidence": [],
            "superseded_by": None,
        }
    ]

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_decision_and_evidence_revalidated_on_the_evaluated_head_pass() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-current-dismissal",
            "severity": "P2",
            "disposition": "dismissed",
            "actionable": False,
            "observed_at_head_sha": "2" * 40,
            "justification": "revalidated on the evaluated HEAD",
            "decided_by": "reviewer-1",
            "decided_at_head_sha": "2" * 40,
            "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "2" * 40}],
            "superseded_by": None,
        }
    ]

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.READY


def test_new_finding_on_the_evaluated_head_remains_representable() -> None:
    payload = _readiness()
    payload["findings"] = [
        {
            "finding_id": "finding-current-new",
            "severity": "P3",
            "disposition": "new",
            "actionable": True,
            "observed_at_head_sha": "2" * 40,
            "justification": None,
            "decided_by": None,
            "decided_at_head_sha": None,
            "evidence": [],
            "superseded_by": None,
        }
    ]

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.READY


def test_stale_readiness_keeps_findings_bound_to_the_evaluated_identity() -> None:
    payload = _readiness()
    payload["evaluated_head_sha"] = "3" * 40
    payload["evaluated_identity"]["head_sha"] = "3" * 40  # type: ignore[index]
    evaluated_identity = _validate_json(RunIdentityV2, payload["evaluated_identity"])
    payload.update(
        state="stale",
        evaluated_run_id=compute_run_id(evaluated_identity),
        reason_codes=["head_mismatch"],
        findings=[
            {
                "finding_id": "finding-stale-context",
                "severity": "P3",
                "disposition": "new",
                "actionable": True,
                "observed_at_head_sha": "3" * 40,
                "justification": None,
                "decided_by": None,
                "decided_at_head_sha": None,
                "evidence": [],
                "superseded_by": None,
            }
        ],
    )
    payload["checks"][0]["head_sha"] = "3" * 40  # type: ignore[index]

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.STALE


@pytest.mark.parametrize("severity", ["P0", "P1", "P2"])
def test_new_blocking_finding_is_manual_required_with_a_healthy_pipeline(severity: str) -> None:
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": "finding-new",
            }
        ],
        findings=[_new_lifecycle_finding(severity=severity)],
    )

    parsed = _validate_json(ReviewReadinessV2, payload)

    assert parsed.state is ReadinessStateV2.MANUAL_REQUIRED
    assert parsed.pipeline.degraded is False
    assert parsed.blockers[0].finding_id == "finding-new"


@pytest.mark.parametrize("finding_id", [None, "finding-missing"])
def test_confirmation_blocker_requires_an_existing_finding_id(finding_id: str | None) -> None:
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": finding_id,
            }
        ],
        findings=[_new_lifecycle_finding()],
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_new_p3_cannot_require_blocking_confirmation() -> None:
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": "finding-p3",
            }
        ],
        findings=[_new_lifecycle_finding(finding_id="finding-p3", severity="P3")],
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_new_finding_alone_cannot_create_blocked_code() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_code",
        reason_codes=["confirmed_code_finding"],
        blockers=[
            {
                "blocker_id": "code-1",
                "reason_code": "confirmed_code_finding",
                "active": True,
                "finding_id": "finding-new",
            }
        ],
        findings=[_new_lifecycle_finding()],
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


@pytest.mark.parametrize("severity", ["P0", "P1", "P2"])
def test_ready_remains_forbidden_with_a_new_blocking_finding(severity: str) -> None:
    payload = _readiness()
    payload["findings"] = [_new_lifecycle_finding(severity=severity)]

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


@pytest.mark.parametrize("disposition", ["fixed", "dismissed"])
def test_terminal_finding_does_not_remain_blocking(disposition: str) -> None:
    finding = {
        "finding_id": f"finding-{disposition}",
        "severity": "P2",
        "observed_at_head_sha": "2" * 40,
        "disposition": disposition,
        "actionable": False,
        "justification": "revalidated terminal decision" if disposition == "dismissed" else None,
        "decided_by": "reviewer-1",
        "decided_at_head_sha": "2" * 40,
        "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "2" * 40}],
        "superseded_by": None,
    }
    payload = _readiness()
    payload["findings"] = [finding]

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.READY


def test_confirmed_finding_takes_precedence_without_losing_new_finding_audit() -> None:
    payload = _readiness()
    payload.update(
        state="blocked_code",
        reason_codes=["confirmed_code_finding"],
        blockers=[
            {
                "blocker_id": "code-1",
                "reason_code": "confirmed_code_finding",
                "active": True,
                "finding_id": "finding-confirmed",
            }
        ],
        findings=[
            _confirmed_lifecycle_finding(),
            _new_lifecycle_finding(finding_id="finding-pending", severity="P1"),
        ],
    )

    parsed = _validate_json(ReviewReadinessV2, payload)

    assert parsed.state is ReadinessStateV2.BLOCKED_CODE
    assert {finding.finding_id for finding in parsed.findings} == {
        "finding-confirmed",
        "finding-pending",
    }


def test_manual_confirmation_can_coexist_with_structured_pipeline_cause() -> None:
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["model_uncertainty", "finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "manual-1",
                "reason_code": "model_uncertainty",
                "active": True,
                "finding_id": None,
            },
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": "finding-new",
            },
        ],
        pipeline={
            "degraded": True,
            "causes": [
                {
                    "reason_code": "model_uncertainty",
                    "component": "provider-response",
                    "detail": "response needs human review",
                }
            ],
        },
        findings=[_new_lifecycle_finding()],
    )

    parsed = _validate_json(ReviewReadinessV2, payload)

    assert parsed.state is ReadinessStateV2.MANUAL_REQUIRED
    assert {cause.reason_code for cause in parsed.pipeline.causes} == {
        ReadinessReasonV2.MODEL_UNCERTAINTY
    }


@pytest.mark.parametrize(
    "disposition",
    ["confirmed", "fixed", "dismissed", "superseded", "stale"],
)
def test_confirmation_blocker_rejects_every_non_new_disposition(disposition: str) -> None:
    finding = {
        "finding_id": "finding-not-new",
        "severity": "P2",
        "observed_at_head_sha": "2" * 40,
        "disposition": disposition,
        "actionable": disposition == "confirmed",
        "justification": "dismissed after review" if disposition == "dismissed" else None,
        "decided_by": "reviewer-1",
        "decided_at_head_sha": "2" * 40,
        "evidence": [{"kind": "test", "reference": "pytest-v2", "head_sha": "2" * 40}]
        if disposition in {"fixed", "dismissed"}
        else [],
        "superseded_by": "finding-successor" if disposition == "superseded" else None,
    }
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": "finding-not-new",
            }
        ],
        findings=[finding],
    )

    with pytest.raises(ValidationError):
        _validate_json(ReviewReadinessV2, payload)


def test_manual_required_preserves_partial_findings_for_audit() -> None:
    payload = _readiness()
    payload.update(
        state="manual_required",
        reason_codes=["model_uncertainty", "finding_confirmation_required"],
        blockers=[
            {
                "blocker_id": "manual-1",
                "reason_code": "model_uncertainty",
                "active": True,
                "finding_id": None,
            },
            {
                "blocker_id": "confirmation-1",
                "reason_code": "finding_confirmation_required",
                "active": True,
                "finding_id": "finding-partial",
            },
        ],
        pipeline={
            "degraded": True,
            "causes": [
                {
                    "reason_code": "model_uncertainty",
                    "component": "provider-response",
                    "detail": "human review required",
                }
            ],
        },
        findings=[
            {
                "finding_id": "finding-partial",
                "severity": "P1",
                "observed_at_head_sha": "2" * 40,
                "disposition": "new",
                "actionable": True,
                "justification": None,
                "decided_by": None,
                "decided_at_head_sha": None,
                "evidence": [],
                "superseded_by": None,
            }
        ],
    )

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.MANUAL_REQUIRED


def test_stale_explicitly_represents_run_identity_divergence() -> None:
    payload = _readiness()
    payload["evaluated_identity"]["policy_hash"] = "0" * 64  # type: ignore[index]
    evaluated_identity = _validate_json(RunIdentityV2, payload["evaluated_identity"])
    payload.update(
        state="stale",
        evaluated_run_id=compute_run_id(evaluated_identity),
        reason_codes=["identity_mismatch"],
    )

    assert _validate_json(ReviewReadinessV2, payload).state is ReadinessStateV2.STALE


def test_schema_export_check_is_read_only_and_fresh_process_stable() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/export-agent-review-v2-schemas.py", "--check"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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
        committed_text = (SCHEMAS / filename).read_text(encoding="utf-8")
        committed = json.loads(committed_text)
        assert committed == schema
        assert committed_text == render_v2_json_schema_text(schema)
        assert all("app__" not in name and not name.endswith(("__1", "__2")) for name in schema.get("$defs", {}))
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
