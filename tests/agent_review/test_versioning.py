from __future__ import annotations

import pytest

from app.agent_review.contracts_v2 import ResponseBindingError
from app.agent_review.schemas import ChunkResponse
from app.agent_review.versioning import (
    MIXED_CONTRACT_VERSIONS_REASON_V2,
    UNSUPPORTED_CONTRACT_VERSION_REASON_V2,
    ContractVersionV2,
    select_contract_version,
)


def _v2_payload_marker() -> dict[str, object]:
    return {"schema_id": "agent-review.chunk-payload.v2", "schema_version": 2}


def _v1_payload_marker() -> dict[str, object]:
    return {"schema_id": "agent-review.chunk-payload.v1", "schema_version": 1}


def _v2_response_marker() -> dict[str, object]:
    return {"schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2}


def _v1_response_marker() -> dict[str, object]:
    return {"schema_version": 1}


def test_select_contract_version_accepts_v2_payload_and_response_agreeing_with_request() -> None:
    version = select_contract_version(
        requested="v2",
        payload_raw=_v2_payload_marker(),
        response_raw=_v2_response_marker(),
    )
    assert version is ContractVersionV2.V2


def test_select_contract_version_accepts_v1_payload_without_response() -> None:
    version = select_contract_version(
        requested="v1",
        payload_raw=_v1_payload_marker(),
        response_raw=None,
    )
    assert version is ContractVersionV2.V1


def test_select_contract_version_accepts_v1_payload_and_response_agreeing_with_request() -> None:
    version = select_contract_version(
        requested="v1",
        payload_raw=_v1_payload_marker(),
        response_raw=_v1_response_marker(),
    )
    assert version is ContractVersionV2.V1


@pytest.mark.parametrize("requested", [None, "", "v3", "V2", "1", "v2 "])
def test_select_contract_version_rejects_an_unrecognized_requested_string(
    requested: str | None,
) -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested=requested,
            payload_raw=_v2_payload_marker(),
            response_raw=_v2_response_marker(),
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_select_contract_version_rejects_a_payload_with_no_recognizable_markers() -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw={},
            response_raw=_v2_response_marker(),
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_select_contract_version_rejects_a_payload_with_unknown_schema_id_and_version() -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw={"schema_id": "something-else.v9", "schema_version": 9},
            response_raw=None,
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_select_contract_version_rejects_a_response_with_no_recognizable_markers() -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw=_v2_payload_marker(),
            response_raw={"nonsense": True},
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_select_contract_version_rejects_v2_request_with_v1_shaped_payload() -> None:
    """Item 12 of the regression matrix: a v1 envelope/payload must never be
    silently accepted by a v2-requesting call site."""

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw=_v1_payload_marker(),
            response_raw=None,
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_select_contract_version_rejects_v2_payload_mixed_with_v1_response() -> None:
    """Item 15 of the regression matrix: payload and response individually
    recognized, but as different known versions."""

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw=_v2_payload_marker(),
            response_raw=_v1_response_marker(),
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_select_contract_version_rejects_v1_payload_mixed_with_v2_response() -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v1",
            payload_raw=_v1_payload_marker(),
            response_raw=_v2_response_marker(),
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_select_contract_version_rejects_v1_request_with_v2_shaped_payload() -> None:
    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v1",
            payload_raw=_v2_payload_marker(),
            response_raw=None,
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_v1_chunk_response_model_independently_rejects_a_v2_shaped_envelope() -> None:
    """Item 13 of the regression matrix: v1's own (untouched) ChunkResponse
    model must keep rejecting v2-shaped input on its own -- no change to
    chunk_result_parser.py or schemas.py is required or made for this."""

    v2_shaped_response = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "chunk_id": "chunk-001",
        # v1 requires `semantic_group`; the v2 envelope shape has no such
        # top-level field, so this must fail validation on its own.
    }
    with pytest.raises(Exception):
        ChunkResponse.model_validate(v2_shaped_response)


# -- post-merge finding (Codex review of PR #97) ---------------------------


def test_select_contract_version_rejects_an_explicit_null_schema_id() -> None:
    """{"schema_id": null} is not the same document as one that never
    declared schema_id at all -- an explicit null is a corrupted/foreign
    shape and must not be silently accepted as v1."""

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v1",
            payload_raw=_v1_payload_marker(),
            response_raw={"schema_id": None, "schema_version": 1},
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


# -- #200-C-WIRE: the Router semantic result is a second legitimate v2
# -- response artifact (P1 discussion_r3858955407) -------------------------


def _v2_semantic_result_marker() -> dict[str, object]:
    """``ChunkReviewResultV2``: what the Router returns as assistant content.

    Distinct artifact from the historical ``chunk-response-envelope.v2``,
    but the same contract *line* -- both are ``ContractVersionV2.V2``.
    """

    return {"schema_id": "agent-review.chunk-response.v2", "schema_version": 2}


def test_select_contract_version_accepts_the_v2_semantic_result_as_v2() -> None:
    """R-VERSION-2: the Router's own response artifact is recognised v2."""

    version = select_contract_version(
        requested="v2",
        payload_raw=_v2_payload_marker(),
        response_raw=_v2_semantic_result_marker(),
    )
    assert version is ContractVersionV2.V2


def test_select_contract_version_still_accepts_the_historical_v2_envelope() -> None:
    """R-VERSION-3: recognising the semantic result must not displace the
    envelope marker the offline path has always depended on."""

    version = select_contract_version(
        requested="v2",
        payload_raw=_v2_payload_marker(),
        response_raw=_v2_response_marker(),
    )
    assert version is ContractVersionV2.V2


@pytest.mark.parametrize(
    "response_raw",
    [
        {"schema_id": "agent-review.chunk-response.v9", "schema_version": 9},
        {"schema_id": "agent-review.chunk-response.v2", "schema_version": 9},
        {"schema_id": "agent-review.chunk-response.v9", "schema_version": 2},
        {"schema_id": "foreign.review-result.v2", "schema_version": 2},
    ],
)
def test_select_contract_version_rejects_an_unknown_semantic_result_marker(
    response_raw: dict[str, object],
) -> None:
    """R-VERSION-4: an unknown/foreign result is *unsupported*, never mixed.

    ``mixed`` asserts that both sides are known and disagree; claiming it
    for an unrecognised document would invent knowledge.
    """

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw=_v2_payload_marker(),
            response_raw=response_raw,
        )
    assert excinfo.value.reason_code == UNSUPPORTED_CONTRACT_VERSION_REASON_V2


def test_select_contract_version_reports_a_v2_semantic_result_against_v1_as_mixed() -> None:
    """R-VERSION-5: known-v2 result under a v1 request/payload is mixed."""

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v1",
            payload_raw=_v1_payload_marker(),
            response_raw=_v2_semantic_result_marker(),
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2


def test_select_contract_version_reports_a_v1_response_against_v2_as_mixed() -> None:
    """R-VERSION-1 (authority half): a *known* v1 response under a v2
    request/payload is mixed -- the information the Router path currently
    destroys by reporting a generic domain-parse failure."""

    with pytest.raises(ResponseBindingError) as excinfo:
        select_contract_version(
            requested="v2",
            payload_raw=_v2_payload_marker(),
            response_raw=_v1_response_marker(),
        )
    assert excinfo.value.reason_code == MIXED_CONTRACT_VERSIONS_REASON_V2
