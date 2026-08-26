from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent_review.consumer_v2 import (
    TRANSPORT_FAILURE_REASON_V2,
    bind_chunk_response_v2,
    load_and_bind_chunk_response_v2,
)
from app.agent_review.contracts_v2 import (
    PAYLOAD_CONTRACT_INVALID_REASON_V2,
    RESPONSE_CONTRACT_INVALID_REASON_V2,
    RESPONSE_SCOPE_MISMATCH_REASON_V2,
    ChunkFindingV2,
    ChunkPayloadV2,
    ResponseBindingError,
    RunIdentityV2,
    compute_payload_sha256_v2,
    compute_response_sha256_v2,
    compute_run_id,
    validate_chunk_response_envelope_v2,
)
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.versioning import (
    UNSUPPORTED_CONTRACT_VERSION_REASON_V2,
    select_contract_version,
)


def _identity(**overrides: object) -> dict[str, object]:
    identity: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 83,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    identity.update(overrides)
    return identity


def _coverage(
    files: list[str] | None = None, must_review: list[str] | None = None
) -> dict[str, object]:
    files = list(files or ["app/service.py"])
    must_review = list(files) if must_review is None else list(must_review)
    return {
        "status": "complete",
        "expected_files": tuple(files),
        "reviewed_files": tuple(files),
        "partially_reviewed_files": (),
        "missing_files": (),
        "must_review_files": tuple(must_review),
        "missing_must_review_files": (),
        "degradation_causes": (),
    }


def _payload_raw(
    *,
    chunk_id: str = "chunk-001",
    identity: dict[str, object] | None = None,
    coverage: dict[str, object] | None = None,
) -> dict[str, object]:
    identity = identity or _identity()
    payload: dict[str, object] = {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": "0" * 64,
        "identity": identity,
        "chunk_id": chunk_id,
        "semantic_group": "api_schema_contract",
        "payload_sha256": "0" * 64,
        "coverage": coverage or _coverage(),
        "artifact_references": [],
        "contract_references": [],
    }
    payload["run_id"] = compute_run_id(RunIdentityV2.model_validate(identity))
    payload["payload_sha256"] = compute_payload_sha256_v2(payload)
    return payload


def _payload(**kwargs: object) -> ChunkPayloadV2:
    return ChunkPayloadV2.model_validate(_payload_raw(**kwargs))


def _finding(
    file_path: str = "app/service.py", finding_id: str = "finding-001"
) -> dict[str, object]:
    return {
        "finding_id": finding_id,
        "severity": "P2",
        "title": "counter-overlap",
        "file_path": file_path,
        "line_start": 10,
        "line_end": 12,
        "evidence": "non-disjoint-counters",
        "impact": "double-counting",
        "confidence": "high",
        "contract_ids": (),
        "disposition": "new",
    }


def _finding_model(**overrides: object) -> ChunkFindingV2:
    return ChunkFindingV2.model_validate(_finding(**overrides))


def _success_envelope_raw(
    payload: ChunkPayloadV2,
    *,
    findings: list[dict[str, object]],
    coverage: dict[str, object] | None = None,
    run_id: str | None = None,
    chunk_id: str | None = None,
    payload_sha256: str | None = None,
    head_sha: str | None = None,
    attempt: int = 1,
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": run_id if run_id is not None else payload.run_id,
        "chunk_id": chunk_id if chunk_id is not None else payload.chunk_id,
        "payload_sha256": (
            payload_sha256 if payload_sha256 is not None else payload.payload_sha256
        ),
        "head_sha": head_sha if head_sha is not None else payload.identity.head_sha,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": attempt,
        "request_id": "req-83-1",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "review-complete",
            "findings": findings,
            "coverage": coverage
            if coverage is not None
            else json.loads(payload.coverage.model_dump_json()),
            "limitations": [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _error_envelope_raw(
    payload: ChunkPayloadV2,
    *,
    reason_code: str = "transport_failure",
    response_received: bool = False,
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "error",
        "run_id": payload.run_id,
        "chunk_id": payload.chunk_id,
        "payload_sha256": payload.payload_sha256,
        "head_sha": payload.identity.head_sha,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": "req-83-err",
        "finish_reason": "error",
        "response_received": response_received,
        "response_sha256": None,
        "error": {"reason_code": reason_code, "retryable": True},
    }
    if response_received:
        envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


# -- 1. cross-run -----------------------------------------------------------


def test_bind_rejects_response_bound_to_a_different_run() -> None:
    payload_a = _payload(chunk_id="chunk-001")
    payload_b = _payload(chunk_id="chunk-001", identity=_identity(pr_number=999))
    envelope_b = _success_envelope_raw(payload_b, findings=[])
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope_b, payload=payload_a)
    assert excinfo.value.reason_code == "run_id_mismatch"


# -- 2. stale HEAD ------------------------------------------------------------


def test_bind_rejects_response_with_a_stale_head_sha() -> None:
    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[], head_sha="9" * 40)
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == "head_sha_mismatch"


# -- 3. different but individually valid payload ------------------------------


def test_bind_rejects_a_different_but_individually_valid_payload() -> None:
    payload_a = _payload(chunk_id="chunk-001")
    payload_b = _payload(
        chunk_id="chunk-001",
        coverage=_coverage(files=["app/other.py"], must_review=["app/other.py"]),
    )
    envelope_b = _success_envelope_raw(payload_b, findings=[])
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope_b, payload=payload_a)
    assert excinfo.value.reason_code == "payload_sha256_mismatch"


# -- 4. chunk_id divergent -----------------------------------------------------


def test_bind_rejects_envelope_chunk_id_diverging_from_payload() -> None:
    payload = _payload(chunk_id="chunk-001")
    envelope = _success_envelope_raw(payload, findings=[], chunk_id="chunk-002")
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == "chunk_id_mismatch"


# -- 5. response hash adulterated ---------------------------------------------


def test_bind_rejects_a_model_copy_with_a_stale_response_hash() -> None:
    payload = _payload()
    envelope = validate_chunk_response_envelope_v2(
        _success_envelope_raw(payload, findings=[_finding()])
    )
    tampered = envelope.model_copy(update={"response_sha256": "f" * 64})
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=tampered, payload=payload)
    assert excinfo.value.reason_code == RESPONSE_CONTRACT_INVALID_REASON_V2


# -- 6. payload mutated --------------------------------------------------------


def test_bind_rejects_a_payload_model_copy_with_a_stale_hash() -> None:
    payload = _payload()
    tampered_payload = payload.model_copy(update={"chunk_id": "chunk-999"})
    envelope = _success_envelope_raw(payload, findings=[])
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=tampered_payload)
    assert excinfo.value.reason_code == PAYLOAD_CONTRACT_INVALID_REASON_V2


def test_bind_result_is_immune_to_nested_mutation_of_the_caller_payload_after_binding() -> None:
    """The caller's own payload.coverage.reviewed_files can no longer be
    mutated at all (tuple, not list -- Codex review of #97), which by
    itself already guarantees the bound result cannot be corrupted through
    this vector; confirmed directly rather than through an "attempt then
    check unaffected" pattern that no longer applies once the attempt
    itself raises."""

    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[_finding()])
    bind_chunk_response_v2(envelope=envelope, payload=payload)

    with pytest.raises(AttributeError):
        payload.coverage.reviewed_files.append("app/other.py")


# -- 7. envelope mutated --------------------------------------------------------


def test_bind_result_is_immune_to_nested_mutation_of_the_caller_envelope_after_binding() -> None:
    """Unlike ChunkCoverageV2/ChunkFindingV2 (Codex review of #97),
    ChunkReviewResultV2.findings itself is still a genuinely mutable
    list[ChunkFindingV2] -- out of that finding's scope -- so this
    mutation is still possible and must still be defended against by
    bind_chunk_response_v2 building its own bound state rather than
    aliasing the caller's envelope."""

    payload = _payload()
    envelope = validate_chunk_response_envelope_v2(
        _success_envelope_raw(payload, findings=[_finding()])
    )
    bound = bind_chunk_response_v2(envelope=envelope, payload=payload)

    envelope.result.findings.append(_finding_model(finding_id="finding-injected"))

    assert len(bound.findings) == 1
    assert bound.findings[0].finding_id == "finding-001"


# -- 8/9. coverage promotion ----------------------------------------------------


def test_bind_rejects_coverage_promoted_from_missing_to_reviewed() -> None:
    payload_coverage = {
        "status": "partial",
        "expected_files": ("app/service.py", "app/other.py"),
        "reviewed_files": ("app/service.py",),
        "partially_reviewed_files": (),
        "missing_files": ("app/other.py",),
        "must_review_files": ("app/service.py", "app/other.py"),
        "missing_must_review_files": ("app/other.py",),
        "degradation_causes": (),
    }
    payload = _payload(coverage=payload_coverage)
    response_coverage = dict(payload_coverage)
    response_coverage["status"] = "complete"
    response_coverage["reviewed_files"] = ("app/service.py", "app/other.py")
    response_coverage["missing_files"] = ()
    response_coverage["missing_must_review_files"] = ()
    envelope = _success_envelope_raw(payload, findings=[], coverage=response_coverage)
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == RESPONSE_SCOPE_MISMATCH_REASON_V2


def test_bind_rejects_coverage_promoted_from_partial_to_reviewed() -> None:
    payload_coverage = {
        "status": "partial",
        "expected_files": ("app/service.py", "app/other.py"),
        "reviewed_files": ("app/service.py",),
        "partially_reviewed_files": ("app/other.py",),
        "missing_files": (),
        "must_review_files": ("app/service.py", "app/other.py"),
        "missing_must_review_files": ("app/other.py",),
        "degradation_causes": (),
    }
    payload = _payload(coverage=payload_coverage)
    response_coverage = dict(payload_coverage)
    response_coverage["status"] = "complete"
    response_coverage["reviewed_files"] = ["app/service.py", "app/other.py"]
    response_coverage["partially_reviewed_files"] = []
    response_coverage["missing_must_review_files"] = []
    envelope = _success_envelope_raw(payload, findings=[], coverage=response_coverage)
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == RESPONSE_SCOPE_MISMATCH_REASON_V2


# -- 10. must_review_files divergence -------------------------------------------


def test_bind_rejects_a_must_review_files_divergence() -> None:
    payload = _payload()
    coverage = _coverage()
    coverage["must_review_files"] = []
    envelope = _success_envelope_raw(payload, findings=[], coverage=coverage)
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == RESPONSE_SCOPE_MISMATCH_REASON_V2


# -- 11. finding outside the payload universe -----------------------------------


def test_bind_rejects_a_finding_outside_the_payload_file_universe() -> None:
    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[_finding(file_path="app/outside.py")])
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == RESPONSE_SCOPE_MISMATCH_REASON_V2


def test_bind_rejects_a_finding_naming_a_contract_absent_from_the_payload() -> None:
    """The common scope authority applies to the historical offline binder too."""

    payload = _payload()
    finding = _finding()
    finding["contract_ids"] = ("contract.not-supplied",)
    envelope = _success_envelope_raw(payload, findings=[finding])

    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)

    assert excinfo.value.reason_code == RESPONSE_SCOPE_MISMATCH_REASON_V2


# -- 16. transport failure --------------------------------------------------------


def test_load_and_bind_rejects_a_missing_response_file(tmp_path: Path) -> None:
    payload = _payload()
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    response_path = tmp_path / "response.json"  # never written

    with pytest.raises(ResponseBindingError) as excinfo:
        load_and_bind_chunk_response_v2(payload_path=payload_path, response_path=response_path)
    assert excinfo.value.reason_code == TRANSPORT_FAILURE_REASON_V2


def test_load_and_bind_rejects_an_illegible_json_response_file(tmp_path: Path) -> None:
    """A response file that exists but is not valid JSON is a distinct
    failure from an absent file: bytes were received, they just cannot be
    bound. It must still surface a stable reason code and never findings."""

    payload = _payload()
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    response_path = tmp_path / "response.json"
    response_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ResponseBindingError) as excinfo:
        load_and_bind_chunk_response_v2(payload_path=payload_path, response_path=response_path)
    assert excinfo.value.reason_code == RESPONSE_CONTRACT_INVALID_REASON_V2


def test_bind_rejects_a_valid_transport_failure_error_envelope_with_no_findings() -> None:
    payload = _payload()
    envelope = _error_envelope_raw(
        payload, reason_code="transport_failure", response_received=False
    )
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == TRANSPORT_FAILURE_REASON_V2


def test_bind_passes_through_a_received_error_envelopes_own_reason_code() -> None:
    payload = _payload()
    envelope = _error_envelope_raw(
        payload, reason_code="schema_failure", response_received=True
    )
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == "schema_failure"


# -- 17. retries out of order / statelessness ------------------------------------


def test_bind_is_stateless_across_out_of_order_attempts() -> None:
    payload = _payload()
    envelope_attempt_2 = _success_envelope_raw(
        payload, findings=[_finding(finding_id="f-attempt-2")], attempt=2
    )
    envelope_attempt_1 = _success_envelope_raw(
        payload, findings=[_finding(finding_id="f-attempt-1")], attempt=1
    )

    bound_second_call_first = bind_chunk_response_v2(envelope=envelope_attempt_2, payload=payload)
    bound_first_call_second = bind_chunk_response_v2(envelope=envelope_attempt_1, payload=payload)

    assert bound_second_call_first.findings[0].finding_id == "f-attempt-2"
    assert bound_first_call_second.findings[0].finding_id == "f-attempt-1"


# -- 18. replay of a previous run's response -------------------------------------


def test_bind_rejects_replay_of_a_previous_runs_response() -> None:
    old_payload = _payload(identity=_identity(pr_number=1))
    old_envelope = _success_envelope_raw(old_payload, findings=[_finding()])
    new_payload = _payload(identity=_identity(pr_number=2))

    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=old_envelope, payload=new_payload)
    assert excinfo.value.reason_code == "run_id_mismatch"


# -- 19. two chunks of the same run swapped --------------------------------------


def test_bind_rejects_chunk_a_response_bound_against_chunk_b_payload() -> None:
    identity = _identity()
    payload_a = _payload(chunk_id="chunk-a", identity=identity)
    payload_b = _payload(chunk_id="chunk-b", identity=identity)
    envelope_a = _success_envelope_raw(payload_a, findings=[_finding()])

    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope_a, payload=payload_b)
    assert excinfo.value.reason_code == "chunk_id_mismatch"


# -- 20. binding failure never leaks a partially-bound object -------------------


def test_binding_failure_produces_no_bound_object_reachable_by_the_parser() -> None:
    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[_finding()], chunk_id="chunk-wrong")

    bound = None
    try:
        bound = bind_chunk_response_v2(envelope=envelope, payload=payload)
    except ResponseBindingError:
        pass
    assert bound is None

    with pytest.raises(TypeError):
        parse_bound_chunk_response_v2(envelope)  # raw dict, never bound


# -- 23. reason-code precedence under multiple simultaneous failures ------------


def test_precedence_prefers_chunk_id_mismatch_over_head_and_scope_mismatch() -> None:
    payload = _payload()
    bad_coverage = _coverage()
    bad_coverage["must_review_files"] = []  # would also trigger response_scope_mismatch
    envelope = _success_envelope_raw(
        payload,
        findings=[_finding(file_path="app/outside.py")],  # would also trigger scope mismatch
        coverage=bad_coverage,
        chunk_id="chunk-wrong",  # earlier in the comparison order
        head_sha="9" * 40,  # would also trigger head_sha_mismatch
    )
    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert excinfo.value.reason_code == "chunk_id_mismatch"


def test_precedence_prefers_version_selection_over_a_deeper_payload_defect() -> None:
    payload_raw = _payload_raw()
    payload_raw["payload_sha256"] = "f" * 64  # also individually invalid

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(requested="v3", payload_raw=payload_raw, response_raw=None)
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


# -- 24. sanitized, closed reason-code surface -----------------------------------


def test_reason_codes_never_interpolate_raw_content_and_stay_within_a_closed_list() -> None:
    known_reason_codes = {
        "response_contract_invalid",
        "payload_contract_invalid",
        "response_scope_mismatch",
        "run_id_mismatch",
        "chunk_id_mismatch",
        "payload_sha256_mismatch",
        "head_sha_mismatch",
        "transport_failure",
        "schema_failure",
        "policy_failure",
        "model_uncertainty",
    }
    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[], chunk_id="chunk-wrong")

    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope, payload=payload)

    assert excinfo.value.reason_code in known_reason_codes
    assert str(excinfo.value) == excinfo.value.reason_code
    assert "/" not in excinfo.value.reason_code
    assert "Bearer" not in str(excinfo.value)
    assert "Traceback" not in str(excinfo.value)


# -- post-merge findings (Codex review of PR #97) --------------------------


def test_bound_coverage_property_string_lists_are_genuinely_immutable() -> None:
    """A Codex review found that a bound object's own coverage was
    corruptible through its own returned reference: frozen=True on
    ChunkCoverageV2 blocked reassigning the `reviewed_files` field, but not
    mutating the list object it already held (`.reviewed_files.append(...)`
    silently succeeded). `.coverage`'s existing `model_copy(deep=True)`
    defense limited the blast radius to that one access, but the mutation
    itself was still possible. Fields are now tuple[...], so the mutation
    is impossible at the type level -- stronger than copy isolation alone."""

    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[_finding()])
    bound = bind_chunk_response_v2(envelope=envelope, payload=payload)

    with pytest.raises(AttributeError):
        bound.coverage.reviewed_files.append("app/injected.py")


def test_bound_finding_property_contract_ids_are_genuinely_immutable() -> None:
    payload = _payload()
    envelope = _success_envelope_raw(payload, findings=[_finding()])
    bound = bind_chunk_response_v2(envelope=envelope, payload=payload)

    with pytest.raises(AttributeError):
        bound.findings[0].contract_ids.append("injected-contract")


def test_load_and_bind_rejects_invalid_utf8_in_the_response_file(tmp_path: Path) -> None:
    """UnicodeDecodeError derives from ValueError, not OSError -- reading
    invalid UTF-8 bytes must still surface a stable ResponseBindingError,
    never an unhandled exception escaping the closed reason-code surface."""

    payload = _payload()
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(payload.model_dump_json(), encoding="utf-8")
    response_path = tmp_path / "response.json"
    response_path.write_bytes(b"\xff\xfe\x00invalid-utf8")

    with pytest.raises(ResponseBindingError) as excinfo:
        load_and_bind_chunk_response_v2(payload_path=payload_path, response_path=response_path)
    assert excinfo.value.reason_code == RESPONSE_CONTRACT_INVALID_REASON_V2


def test_load_and_bind_rejects_invalid_utf8_in_the_payload_file(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_bytes(b"\xff\xfe\x00invalid-utf8")
    response_path = tmp_path / "response.json"
    response_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ResponseBindingError) as excinfo:
        load_and_bind_chunk_response_v2(payload_path=payload_path, response_path=response_path)
    assert excinfo.value.reason_code == PAYLOAD_CONTRACT_INVALID_REASON_V2
