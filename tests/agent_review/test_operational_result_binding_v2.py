"""`#200-F` authority D -- ranges are binding-time material.

The headline test reproduces the `#276` P0 exactly: a valid path, in a file
the chunk really covers, at a line just outside the fragment's range. An
off-by-N line number is ordinary model behaviour, and under the predecessor it
bound cleanly and then detonated in synthesis, aborting the whole run.
"""

from __future__ import annotations

import json

import pytest

from app.agent_review.chunk_result_scope_v2 import (
    FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2,
    UNKNOWN_CHUNK_RESULT_REASON_V2,
)
from app.agent_review.consumer_v2 import BoundChunkResponseV2, bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ChunkPayloadV2,
    ResponseBindingError,
    RunIdentityV2,
    compute_payload_sha256_v2,
    compute_response_sha256_v2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import (
    LineRangeV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2
from app.agent_review.operational_result_binding_v2 import (
    FINDING_PATH_OUTSIDE_CHUNK_FRAGMENTS_REASON_V2,
    bind_offline_response_with_range_authority_v2,
    validate_result_fragment_ranges_v2,
)

_REVIEWED_PATH_V2 = "app/service.py"
_CHUNK_ID_V2 = "chunk-001"
_FRAGMENT_START_V2 = 10
_FRAGMENT_END_V2 = 20


def _fragment_v2(
    *, path: str = _REVIEWED_PATH_V2, start: int, end: int, diff_sha256: str
) -> dict[str, object]:
    old_range = LineRangeV2(start=start, end=end)
    new_range = LineRangeV2(start=start, end=end)
    return {
        "fragment_id": compute_fragment_id_v2(
            path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha256
        ),
        "path": path,
        "old_range": {"start": start, "end": end},
        "new_range": {"start": start, "end": end},
        "hunk_indexes": [0],
        "diff_chars": 120,
        "diff_sha256": diff_sha256,
        "coverage_required": True,
    }


def _manifest_and_payload_v2(
    *, extra_fragments: list[dict[str, object]] | None = None
) -> tuple[ManifestV2, ChunkPayloadV2]:
    """Build a manifest and a payload that share one identity.

    ``manifest_hash`` lives inside ``RunIdentityV2`` and ``run_id`` is derived
    from that identity, so the material must be hashed *before* the identity
    exists. Building them in any other order silently produces a manifest that
    disagrees with its own payload.
    """
    fragments = [
        _fragment_v2(start=_FRAGMENT_START_V2, end=_FRAGMENT_END_V2, diff_sha256="e" * 64)
    ]
    fragments.extend(extra_fragments or [])
    expected_files = sorted({str(fragment["path"]) for fragment in fragments})

    material_raw = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files,
        "must_review_files": [],
        "fragments": fragments,
        "chunks": [
            {
                "chunk_id": _CHUNK_ID_V2,
                "order_index": 0,
                "semantic_group": "api_schema_contract",
                "fragment_ids": [str(fragment["fragment_id"]) for fragment in fragments],
                "payload_sha256": None,
            }
        ],
        "max_chunks": 8,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_raw)

    identity_raw = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 200,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": compute_manifest_hash_v2_for(material),
        "evidence_hash": "d" * 64,
    }
    identity = RunIdentityV2.model_validate(identity_raw)
    run_id = compute_run_id(identity)

    manifest = ManifestV2.model_validate(
        {**material_raw, "run_id": run_id, "identity": identity_raw}
    )

    coverage = {
        "status": "complete",
        "expected_files": tuple(expected_files),
        "reviewed_files": tuple(expected_files),
        "partially_reviewed_files": (),
        "missing_files": (),
        "must_review_files": (),
        "missing_must_review_files": (),
        "degradation_causes": (),
    }
    payload_raw: dict[str, object] = {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": run_id,
        "identity": identity_raw,
        "chunk_id": _CHUNK_ID_V2,
        "semantic_group": "api_schema_contract",
        "payload_sha256": "0" * 64,
        "coverage": coverage,
        "artifact_references": [],
        "contract_references": [],
    }
    payload_raw["payload_sha256"] = compute_payload_sha256_v2(payload_raw)
    return manifest, ChunkPayloadV2.model_validate(payload_raw)


def _finding_v2(
    *, line_start: int | None, line_end: int | None, file_path: str = _REVIEWED_PATH_V2
) -> dict[str, object]:
    return {
        "finding_id": "finding-001",
        "severity": "P2",
        "title": "off-by-n",
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "evidence": "model-cited-line",
        "impact": "unclear",
        "confidence": "medium",
        "contract_ids": (),
        "disposition": "new",
    }


def _success_envelope_v2(
    payload: ChunkPayloadV2, *, findings: list[dict[str, object]]
) -> dict[str, object]:
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
        "request_id": "req-200-1",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": "review-complete",
            "findings": findings,
            "coverage": json.loads(payload.coverage.model_dump_json()),
            "limitations": [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def test_an_in_range_finding_binds_normally() -> None:
    """Non-vacuity control for every refusal below.

    A range authority that rejected everything would satisfy the negative
    tests and destroy the product.
    """
    manifest, payload = _manifest_and_payload_v2()
    envelope = _success_envelope_v2(
        payload, findings=[_finding_v2(line_start=12, line_end=15)]
    )

    bound = bind_offline_response_with_range_authority_v2(
        envelope=envelope, payload=payload, manifest=manifest
    )

    assert isinstance(bound, BoundChunkResponseV2)
    assert len(bound.findings) == 1
    assert bound.findings[0].line_start == 12


@pytest.mark.parametrize(
    "line_start, line_end",
    [
        (_FRAGMENT_END_V2 + 1, _FRAGMENT_END_V2 + 3),   # entirely past the hunk
        (_FRAGMENT_START_V2 - 3, _FRAGMENT_START_V2 - 1),  # entirely before it
        (_FRAGMENT_START_V2 - 1, _FRAGMENT_END_V2),     # starts one line early
        (_FRAGMENT_START_V2, _FRAGMENT_END_V2 + 1),     # ends one line late
    ],
)
def test_the_276_p0_an_out_of_range_line_is_refused_at_binding(
    line_start: int, line_end: int
) -> None:
    """The exact predecessor P0, now refused where the material enters.

    All four spellings are ordinary off-by-N mistakes, not attacks. Under
    `#276` each bound cleanly and aborted the run from synthesis.
    """
    manifest, payload = _manifest_and_payload_v2()
    envelope = _success_envelope_v2(
        payload, findings=[_finding_v2(line_start=line_start, line_end=line_end)]
    )

    with pytest.raises(ResponseBindingError) as caught:
        bind_offline_response_with_range_authority_v2(
            envelope=envelope, payload=payload, manifest=manifest
        )

    assert caught.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_the_predecessor_binder_still_accepts_what_this_one_refuses() -> None:
    """Proves the defect is real on the merged code, not a straw man.

    ``bind_chunk_response_v2`` is untouched by this slice and still binds an
    out-of-range finding without complaint -- it compares file paths and
    contract ids, never ranges. `#276` shipped a written claim that the binder
    refused out-of-scope findings; this is that claim, executed.
    """
    manifest, payload = _manifest_and_payload_v2()
    envelope = _success_envelope_v2(
        payload,
        findings=[
            _finding_v2(line_start=_FRAGMENT_END_V2 + 5, line_end=_FRAGMENT_END_V2 + 7)
        ],
    )

    leaked = bind_chunk_response_v2(envelope=envelope, payload=payload)
    assert len(leaked.findings) == 1, (
        "the merged binder accepts an out-of-range finding; that is the gap "
        "authority D closes"
    )

    with pytest.raises(ResponseBindingError):
        bind_offline_response_with_range_authority_v2(
            envelope=envelope, payload=payload, manifest=manifest
        )


def test_a_finding_with_no_line_range_is_still_allowed() -> None:
    """File-level findings are legitimate and must not be collateral damage."""
    manifest, payload = _manifest_and_payload_v2()
    envelope = _success_envelope_v2(
        payload, findings=[_finding_v2(line_start=None, line_end=None)]
    )

    bound = bind_offline_response_with_range_authority_v2(
        envelope=envelope, payload=payload, manifest=manifest
    )

    assert bound.findings[0].line_start is None


def test_a_finding_in_a_second_fragment_of_the_same_file_binds() -> None:
    """Ranges are per-fragment, and a chunk may carry several.

    Guards against a naive implementation that checked only the first
    fragment, which would reject perfectly valid findings.
    """
    second = _fragment_v2(start=100, end=110, diff_sha256="f" * 64)
    manifest, payload = _manifest_and_payload_v2(extra_fragments=[second])
    envelope = _success_envelope_v2(
        payload, findings=[_finding_v2(line_start=104, line_end=106)]
    )

    bound = bind_offline_response_with_range_authority_v2(
        envelope=envelope, payload=payload, manifest=manifest
    )

    assert bound.findings[0].line_start == 104


def test_a_finding_between_two_fragments_of_the_same_file_is_refused() -> None:
    """The gap between hunks is not reviewed material.

    Non-vacuity partner to the test above: multi-fragment support must not
    become "any line in the file".
    """
    second = _fragment_v2(start=100, end=110, diff_sha256="f" * 64)
    manifest, payload = _manifest_and_payload_v2(extra_fragments=[second])
    envelope = _success_envelope_v2(
        payload, findings=[_finding_v2(line_start=50, line_end=52)]
    )

    with pytest.raises(ResponseBindingError) as caught:
        bind_offline_response_with_range_authority_v2(
            envelope=envelope, payload=payload, manifest=manifest
        )

    assert caught.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


def test_a_finding_naming_a_file_outside_the_chunk_is_refused_distinctly() -> None:
    """Wrong file and wrong line are different mistakes.

    An operator triaging model behaviour needs to tell "reviewed a file it was
    not shown" from "cited a line outside the hunk", so the reason codes
    differ.
    """
    manifest, payload = _manifest_and_payload_v2()
    result = {
        "schema_id": "agent-review.chunk-response.v2",
        "schema_version": 2,
        "summary": "s",
        "findings": [_finding_v2(line_start=12, line_end=15, file_path="other/file.py")],
        "coverage": json.loads(payload.coverage.model_dump_json()),
        "limitations": [],
    }
    from app.agent_review.contracts_v2 import ChunkReviewResultV2

    with pytest.raises(ResponseBindingError) as caught:
        validate_result_fragment_ranges_v2(
            result=ChunkReviewResultV2.model_validate_json(json.dumps(result)),
            chunk_id=_CHUNK_ID_V2,
            manifest=manifest,
        )

    assert caught.value.reason_code == FINDING_PATH_OUTSIDE_CHUNK_FRAGMENTS_REASON_V2


def test_an_unknown_chunk_id_is_refused_before_any_finding_is_read() -> None:
    """A result for a chunk this manifest does not contain proves nothing."""
    manifest, payload = _manifest_and_payload_v2()
    from app.agent_review.contracts_v2 import ChunkReviewResultV2

    result = ChunkReviewResultV2.model_validate_json(
        json.dumps(
            {
                "schema_id": "agent-review.chunk-response.v2",
                "schema_version": 2,
                "summary": "s",
                "findings": [],
                "coverage": json.loads(payload.coverage.model_dump_json()),
                "limitations": [],
            }
        )
    )

    with pytest.raises(ResponseBindingError) as caught:
        validate_result_fragment_ranges_v2(
            result=result, chunk_id="chunk-does-not-exist", manifest=manifest
        )

    assert caught.value.reason_code == UNKNOWN_CHUNK_RESULT_REASON_V2


def test_the_range_predicate_is_shared_with_synthesis_not_reimplemented() -> None:
    """One definition of "inside the fragment", enforced structurally.

    Two range checks that agreed today would drift, and a binder disagreeing
    with synthesis would recreate the defect in mirror image.
    """
    import app.agent_review.chunk_result_scope_v2 as scope_module
    import app.agent_review.operational_result_binding_v2 as binding_module

    assert (
        binding_module._finding_within_chunk_fragments_v2
        is scope_module._finding_within_chunk_fragments_v2
    )


def _verified_router_result_v2(payload: ChunkPayloadV2, *, findings: list[dict[str, object]]):
    """Build a receipt-verified Router result directly.

    Uses ``_router_receipt_v2``'s own sentinel rather than driving a full
    Router exchange. That module's docstring is explicit that the sentinel is
    an internal-API guard against accidental misuse and *not* a cryptographic
    boundary, so constructing one here tests exactly the seam under
    examination -- what the range authority does with an already-verified
    result -- without dragging receipt verification into a binding test. The
    full path with real receipts is exercised in the product acceptance tests.
    """
    from app.agent_review import _router_receipt_v2
    from app.agent_review.contracts_v2 import ChunkReviewResultV2
    from app.agent_review.review_transport_contract_v2 import (
        ChunkReviewRequestV2,
        compute_request_sha256_v2,
    )

    content_sha256 = "7" * 64
    request_material = {
        "run_id": payload.run_id,
        "chunk_id": payload.chunk_id,
        "head_sha": payload.identity.head_sha,
        "payload_sha256": payload.payload_sha256,
        "content_sha256": content_sha256,
    }
    request = ChunkReviewRequestV2.model_validate(
        {**request_material, "request_sha256": compute_request_sha256_v2(**request_material)}
    )
    result = ChunkReviewResultV2.model_validate_json(
        json.dumps(
            {
                "schema_id": "agent-review.chunk-response.v2",
                "schema_version": 2,
                "summary": "review-complete",
                "findings": findings,
                "coverage": json.loads(payload.coverage.model_dump_json()),
                "limitations": [],
            }
        )
    )
    return _router_receipt_v2._VerifiedRouterResultV2(
        sentinel=_router_receipt_v2._VERIFIED_RESULT_SENTINEL,
        request=request,
        result=result,
    )


def test_the_router_path_gets_the_same_range_authority() -> None:
    """Both transports, per the grant. Neither may be the weaker one.

    Receipt verification proves the Router-side input, execution and output
    relations. It establishes nothing about whether the model cited a line
    inside the hunk it was shown, so a receipt-verified result is scrutinised
    exactly like an offline one.
    """
    from app.agent_review.operational_result_binding_v2 import (
        bind_router_result_with_range_authority_v2,
    )

    manifest, payload = _manifest_and_payload_v2()
    verified = _verified_router_result_v2(
        payload,
        findings=[
            _finding_v2(line_start=_FRAGMENT_END_V2 + 5, line_end=_FRAGMENT_END_V2 + 7)
        ],
    )

    with pytest.raises(ResponseBindingError) as caught:
        bind_router_result_with_range_authority_v2(
            verified=verified, payload=payload, manifest=manifest
        )

    assert caught.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


def test_an_in_range_router_result_still_binds() -> None:
    """Non-vacuity control for the Router path."""
    from app.agent_review.operational_result_binding_v2 import (
        bind_router_result_with_range_authority_v2,
    )

    manifest, payload = _manifest_and_payload_v2()
    verified = _verified_router_result_v2(
        payload, findings=[_finding_v2(line_start=12, line_end=15)]
    )

    bound = bind_router_result_with_range_authority_v2(
        verified=verified, payload=payload, manifest=manifest
    )

    assert isinstance(bound, BoundChunkResponseV2)
    assert bound.findings[0].line_start == 12


def test_a_non_verified_object_cannot_enter_the_router_binder() -> None:
    """Sealed-type discipline survives the extra wrapper layer."""
    from app.agent_review.operational_result_binding_v2 import (
        bind_router_result_with_range_authority_v2,
    )

    manifest, payload = _manifest_and_payload_v2()

    with pytest.raises(ResponseBindingError):
        bind_router_result_with_range_authority_v2(
            verified=object(), payload=payload, manifest=manifest
        )
