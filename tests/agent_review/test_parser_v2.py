from __future__ import annotations

import json

import pytest

from app.agent_review.consumer_v2 import BoundChunkResponseV2, bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    RunIdentityV2,
    compute_payload_sha256_v2,
    compute_response_sha256_v2,
    compute_run_id,
    validate_chunk_response_envelope_v2,
)
from app.agent_review.parser_v2 import ParsedChunkResultV2, parse_bound_chunk_response_v2


def _identity() -> dict[str, object]:
    return {
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


def _payload() -> ChunkPayloadV2:
    identity = _identity()
    raw: dict[str, object] = {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": "0" * 64,
        "identity": identity,
        "chunk_id": "chunk-001",
        "semantic_group": "api_schema_contract",
        "payload_sha256": "0" * 64,
        "coverage": _coverage(),
        "artifact_references": [],
        "contract_references": [],
    }
    raw["run_id"] = compute_run_id(RunIdentityV2.model_validate(identity))
    raw["payload_sha256"] = compute_payload_sha256_v2(raw)
    return ChunkPayloadV2.model_validate(raw)


def _success_envelope_raw(payload: ChunkPayloadV2) -> dict[str, object]:
    envelope: dict[str, object] = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": payload.run_id,
        "chunk_id": payload.chunk_id,
        "payload_sha256": payload.payload_sha256,
        "head_sha": payload.identity.head_sha,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": "req-83-1",
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
                    "contract_ids": [],
                    "disposition": "new",
                }
            ],
            "coverage": json.loads(payload.coverage.model_dump_json()),
            "limitations": [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _bound() -> BoundChunkResponseV2:
    payload = _payload()
    envelope = _success_envelope_raw(payload)
    return bind_chunk_response_v2(envelope=envelope, payload=payload)


def test_parse_bound_chunk_response_exposes_findings_and_coverage() -> None:
    bound = _bound()

    parsed = parse_bound_chunk_response_v2(bound)

    assert isinstance(parsed, ParsedChunkResultV2)
    assert parsed.chunk_id == bound.chunk_id
    assert parsed.run_id == bound.run_id
    assert parsed.head_sha == bound.head_sha
    assert parsed.findings == bound.findings
    assert len(parsed.findings) == 1
    assert parsed.findings[0].finding_id == "finding-001"
    assert parsed.coverage == bound.coverage


# -- 21. direct bypass attempts on the parser ------------------------------------


def test_parse_bound_chunk_response_rejects_a_raw_envelope() -> None:
    payload = _payload()
    envelope_raw = _success_envelope_raw(payload)
    real_envelope = validate_chunk_response_envelope_v2(envelope_raw)

    with pytest.raises(TypeError):
        parse_bound_chunk_response_v2(real_envelope)


def test_parse_bound_chunk_response_rejects_a_plain_dict() -> None:
    payload = _payload()
    envelope_raw = _success_envelope_raw(payload)

    with pytest.raises(TypeError):
        parse_bound_chunk_response_v2(envelope_raw)


def test_parse_bound_chunk_response_rejects_an_unrelated_object() -> None:
    with pytest.raises(TypeError):
        parse_bound_chunk_response_v2(object())


def test_bound_chunk_response_cannot_be_constructed_directly_with_plausible_kwargs() -> None:
    """The sentinel is an internal-API guard, not a cryptographic boundary:
    it only proves that BoundChunkResponseV2() called without going through
    bind_chunk_response_v2 fails fast, as documented in consumer_v2.py."""

    bound = _bound()
    with pytest.raises(TypeError):
        BoundChunkResponseV2(
            sentinel=object(),
            run_id=bound.run_id,
            chunk_id=bound.chunk_id,
            head_sha=bound.head_sha,
            payload_sha256=bound.payload_sha256,
            summary=bound.summary,
            findings=bound.findings,
            coverage=bound.coverage,
            limitations=bound.limitations,
        )
