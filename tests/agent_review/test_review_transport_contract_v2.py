from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ChunkResponseErrorEnvelopeV2,
    ChunkResponseSuccessEnvelopeV2,
    ChunkReviewResultV2,
    compute_response_sha256_v2,
)
from app.agent_review.review_transport_contract_v2 import (
    CONTENT_ECHO_MISMATCH_REASON_V2,
    REQUEST_ECHO_MISMATCH_REASON_V2,
    TRANSPORT_ENVELOPE_INVALID_REASON_V2,
    ChunkReviewRequestV2,
    ChunkReviewTransportEnvelopeV1,
    TransportEchoError,
    compute_request_sha256_v2,
    verify_transport_echo_v1,
)

_RUN_ID = "1" * 64
_CHUNK_ID = "chunk-0000"
_HEAD_SHA = "2" * 40
_PAYLOAD_SHA256 = "3" * 64
_CONTENT_SHA256 = "4" * 64
_PATH = "app/service.py"


def _request(*, content_sha256: str = _CONTENT_SHA256) -> ChunkReviewRequestV2:
    request_sha256 = compute_request_sha256_v2(
        run_id=_RUN_ID, chunk_id=_CHUNK_ID, head_sha=_HEAD_SHA,
        payload_sha256=_PAYLOAD_SHA256, content_sha256=content_sha256,
    )
    return ChunkReviewRequestV2(
        run_id=_RUN_ID, chunk_id=_CHUNK_ID, head_sha=_HEAD_SHA,
        payload_sha256=_PAYLOAD_SHA256, content_sha256=content_sha256,
        request_sha256=request_sha256,
    )


def _success_envelope() -> ChunkResponseSuccessEnvelopeV2:
    coverage = ChunkCoverageV2(
        status="complete", expected_files=(_PATH,), reviewed_files=(_PATH,),
        partially_reviewed_files=(), missing_files=(), must_review_files=(_PATH,),
        missing_must_review_files=(), degradation_causes=(),
    )
    result = ChunkReviewResultV2(
        schema_id="agent-review.chunk-response.v2", schema_version=2,
        summary="looks fine", findings=[], coverage=coverage, limitations=[],
    )
    fields = dict(
        schema_id="agent-review.chunk-response-envelope.v2", schema_version=2,
        source="agent-review-provider-response", run_id=_RUN_ID, chunk_id=_CHUNK_ID,
        payload_sha256=_PAYLOAD_SHA256, head_sha=_HEAD_SHA, provider="test-provider",
        model="test-model", attempt=1, request_id="req-0001", finish_reason="stop",
        response_received=True, status="success", result=result,
    )
    dumped = {**fields, "result": result.model_dump(mode="json"), "response_sha256": None}
    response_sha256 = compute_response_sha256_v2(dumped)
    return ChunkResponseSuccessEnvelopeV2(**fields, response_sha256=response_sha256)


def _error_envelope() -> ChunkResponseErrorEnvelopeV2:
    fields = dict(
        schema_id="agent-review.chunk-response-envelope.v2", schema_version=2,
        source="agent-review-provider-response", run_id=_RUN_ID, chunk_id=_CHUNK_ID,
        payload_sha256=_PAYLOAD_SHA256, head_sha=_HEAD_SHA, provider="test-provider",
        model="test-model", attempt=1, request_id="req-0002", finish_reason="error",
        response_received=False, status="error",
        error={"reason_code": "transport_failure", "retryable": True},
    )
    return ChunkResponseErrorEnvelopeV2(**fields, response_sha256=None)


def _transport_envelope(request: ChunkReviewRequestV2, response) -> ChunkReviewTransportEnvelopeV1:
    return ChunkReviewTransportEnvelopeV1(
        schema_id="agent-review.review-transport-envelope.v1", schema_version=1,
        request_sha256=request.request_sha256, content_sha256=request.content_sha256,
        response=response,
    )


# -- ChunkReviewRequestV2 -----------------------------------------------------


def test_request_hash_is_the_documented_preimage() -> None:
    request = _request()
    expected = compute_request_sha256_v2(
        run_id=_RUN_ID, chunk_id=_CHUNK_ID, head_sha=_HEAD_SHA,
        payload_sha256=_PAYLOAD_SHA256, content_sha256=_CONTENT_SHA256,
    )
    assert request.request_sha256 == expected


def test_request_hash_changes_when_content_sha256_changes() -> None:
    request_a = _request(content_sha256="4" * 64)
    request_b = _request(content_sha256="5" * 64)
    assert request_a.request_sha256 != request_b.request_sha256


def test_request_rejects_a_stale_hash() -> None:
    with pytest.raises(ValidationError):
        ChunkReviewRequestV2(
            run_id=_RUN_ID, chunk_id=_CHUNK_ID, head_sha=_HEAD_SHA,
            payload_sha256=_PAYLOAD_SHA256, content_sha256=_CONTENT_SHA256,
            request_sha256="0" * 64,
        )


# -- verify_transport_echo_v1 --------------------------------------------------


def test_echo_verification_accepts_a_genuine_success_envelope() -> None:
    request = _request()
    envelope = _transport_envelope(request, _success_envelope())
    inner = verify_transport_echo_v1(envelope, request=request)
    assert inner.status == "success"


def test_echo_verification_accepts_a_genuine_error_envelope() -> None:
    request = _request()
    envelope = _transport_envelope(request, _error_envelope())
    inner = verify_transport_echo_v1(envelope, request=request)
    assert inner.status == "error"


def test_echo_verification_rejects_a_tampered_content_sha256() -> None:
    request = _request()
    envelope = _transport_envelope(request, _success_envelope())
    tampered = envelope.model_construct(**{**envelope.model_dump(), "content_sha256": "9" * 64})
    with pytest.raises(TransportEchoError) as exc_info:
        verify_transport_echo_v1(tampered, request=request)
    assert exc_info.value.reason_code == CONTENT_ECHO_MISMATCH_REASON_V2


def test_echo_verification_rejects_a_tampered_request_sha256() -> None:
    request = _request()
    envelope = _transport_envelope(request, _success_envelope())
    tampered = envelope.model_construct(**{**envelope.model_dump(), "request_sha256": "9" * 64})
    with pytest.raises(TransportEchoError) as exc_info:
        verify_transport_echo_v1(tampered, request=request)
    assert exc_info.value.reason_code == REQUEST_ECHO_MISMATCH_REASON_V2


def test_echo_verification_rejects_a_response_produced_over_a_different_sidecar() -> None:
    """The exact scenario D2 exists to close: a syntactically perfect
    response, correctly bound to the SAME payload_sha256/run_id/chunk_id/
    head_sha, that was actually produced over a DIFFERENT ``ReviewContentV2``
    sidecar for that same chunk. ``payload_sha256`` alone (what
    ``consumer_v2.validate_response_binding_v2`` checks) cannot distinguish
    the two sidecars; only the echoed ``content_sha256`` can -- and the far
    end here echoes the WRONG one back, everything else held fixed."""

    real_request = _request(content_sha256="4" * 64)
    envelope_for_the_wrong_sidecar = _transport_envelope(real_request, _success_envelope()).model_copy(
        update={"content_sha256": "6" * 64}
    )
    with pytest.raises(TransportEchoError) as exc_info:
        verify_transport_echo_v1(envelope_for_the_wrong_sidecar, request=real_request)
    assert exc_info.value.reason_code == CONTENT_ECHO_MISMATCH_REASON_V2


def test_echo_verification_rejects_an_invalid_inner_envelope_even_with_correct_hashes() -> None:
    request = _request()
    envelope = ChunkReviewTransportEnvelopeV1.model_construct(
        schema_id="agent-review.review-transport-envelope.v1", schema_version=1,
        request_sha256=request.request_sha256, content_sha256=request.content_sha256,
        response=_success_envelope().model_copy(update={"status": "not-a-real-status"}),
    )
    with pytest.raises(TransportEchoError) as exc_info:
        verify_transport_echo_v1(envelope, request=request)
    assert exc_info.value.reason_code == TRANSPORT_ENVELOPE_INVALID_REASON_V2


def test_echo_verification_returns_a_fresh_copy_not_the_callers_object() -> None:
    request = _request()
    original = _success_envelope()
    envelope = _transport_envelope(request, original)
    inner = verify_transport_echo_v1(envelope, request=request)
    assert inner is not original
    assert inner.model_dump() == original.model_dump()
