"""AgentReview v2 consumer: verified binding before any finding is exposed.

This module never re-implements the identity/scope comparisons already
frozen in ``contracts_v2.validate_response_binding_v2``. It only:

1. obtains freshly revalidated copies of the payload and the response
   envelope through ``contracts_v2``'s own public re-validation entry
   points -- never the caller's original references, so a later mutation
   of the caller's objects cannot retroactively affect an already-bound
   result;
2. calls ``validate_response_binding_v2`` once, unmodified, as the single
   authority for identity and scope;
3. only on success, wraps the result in ``BoundChunkResponseV2``.

``BoundChunkResponseV2``'s sentinel-gated constructor is an internal-API
guard against accidental misuse, not a cryptographic boundary: code with
access to this module's private ``_BINDING_SENTINEL`` could still construct
an instance directly, exactly as code with access to ``contracts_v2``'s
frozen models can already reach around ``frozen=True`` via ``model_copy``.
The real guarantee is operational, not defensive-in-depth: the only
supported, documented way to obtain findings --
``parser_v2.parse_bound_chunk_response_v2`` -- accepts nothing but this
type, and this module exposes no other function that returns findings.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    PAYLOAD_CONTRACT_INVALID_REASON_V2,
    RESPONSE_CONTRACT_INVALID_REASON_V2,
    ChunkCoverageV2,
    ChunkFindingV2,
    ChunkPayloadV2,
    ChunkResponseEnvelopeValueV2,
    ChunkResponseErrorEnvelopeV2,
    ChunkResponseSuccessEnvelopeV2,
    ResponseBindingError,
    validate_chunk_response_envelope_v2,
    validate_response_binding_v2,
)

TRANSPORT_FAILURE_REASON_V2 = "transport_failure"

_BINDING_SENTINEL = object()


class BoundChunkResponseV2:
    """Proof that a response envelope was fully bound to its payload.

    Only constructible by ``bind_chunk_response_v2``. Holds fresh,
    independently-revalidated copies -- never the caller's original
    objects -- and exposes findings only as an immutable tuple.
    """

    __slots__ = (
        "_run_id",
        "_chunk_id",
        "_head_sha",
        "_payload_sha256",
        "_summary",
        "_findings",
        "_coverage",
        "_limitations",
    )

    def __init__(
        self,
        *,
        sentinel: object,
        run_id: str,
        chunk_id: str,
        head_sha: str,
        payload_sha256: str,
        summary: str,
        findings: tuple[ChunkFindingV2, ...],
        coverage: ChunkCoverageV2,
        limitations: tuple[str, ...],
    ) -> None:
        if sentinel is not _BINDING_SENTINEL:
            raise TypeError(
                "BoundChunkResponseV2 must be constructed via bind_chunk_response_v2"
            )
        self._run_id = run_id
        self._chunk_id = chunk_id
        self._head_sha = head_sha
        self._payload_sha256 = payload_sha256
        self._summary = summary
        self._findings = findings
        self._coverage = coverage
        self._limitations = limitations

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def chunk_id(self) -> str:
        return self._chunk_id

    @property
    def head_sha(self) -> str:
        return self._head_sha

    @property
    def payload_sha256(self) -> str:
        return self._payload_sha256

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def findings(self) -> tuple[ChunkFindingV2, ...]:
        return self._findings

    @property
    def coverage(self) -> ChunkCoverageV2:
        return self._coverage

    @property
    def limitations(self) -> tuple[str, ...]:
        return self._limitations


def bind_chunk_response_v2(
    *,
    envelope: ChunkResponseEnvelopeValueV2 | Mapping[str, Any] | str | bytes,
    payload: ChunkPayloadV2,
) -> BoundChunkResponseV2:
    """Verify and bind a response envelope to its payload before any finding
    is reachable.

    Raises ``ResponseBindingError`` (see ``contracts_v2`` reason codes, plus
    a passthrough of the envelope's own structured error reason when the
    envelope is a validly-bound error response) on any failure. Never
    returns a partially-bound object -- either a ``BoundChunkResponseV2`` is
    returned, or an exception propagates.
    """

    try:
        fresh_envelope = validate_chunk_response_envelope_v2(envelope)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ResponseBindingError(RESPONSE_CONTRACT_INVALID_REASON_V2) from exc

    try:
        fresh_payload = ChunkPayloadV2.model_validate_json(
            payload.model_dump_json(), strict=True
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise ResponseBindingError(PAYLOAD_CONTRACT_INVALID_REASON_V2) from exc

    validate_response_binding_v2(fresh_envelope, fresh_payload)

    if isinstance(fresh_envelope, ChunkResponseErrorEnvelopeV2):
        raise ResponseBindingError(fresh_envelope.error.reason_code.value)

    assert isinstance(fresh_envelope, ChunkResponseSuccessEnvelopeV2)
    result = fresh_envelope.result
    return BoundChunkResponseV2(
        sentinel=_BINDING_SENTINEL,
        run_id=fresh_envelope.run_id,
        chunk_id=fresh_envelope.chunk_id,
        head_sha=fresh_envelope.head_sha,
        payload_sha256=fresh_envelope.payload_sha256,
        summary=result.summary,
        findings=tuple(result.findings),
        coverage=result.coverage,
        limitations=tuple(result.limitations),
    )


def load_and_bind_chunk_response_v2(
    *, payload_path: Path, response_path: Path
) -> BoundChunkResponseV2:
    """Load a payload and a response envelope from disk and bind them.

    A missing or unreadable response file is a terminal ``transport_failure``
    -- there are no response bytes to validate at all, mirroring the
    envelope-level ``response_received=false`` case. A response file that
    exists but is not valid JSON, or does not validate as a v2 envelope, is
    a ``response_contract_invalid`` -- bytes were received but they are not
    a bindable response. Neither case is ever interpreted as zero findings
    approved: no ``BoundChunkResponseV2`` is ever produced for either.
    """

    try:
        payload_text = payload_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResponseBindingError(PAYLOAD_CONTRACT_INVALID_REASON_V2) from exc

    try:
        payload = ChunkPayloadV2.model_validate_json(payload_text, strict=True)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ResponseBindingError(PAYLOAD_CONTRACT_INVALID_REASON_V2) from exc

    try:
        response_text = response_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResponseBindingError(TRANSPORT_FAILURE_REASON_V2) from exc

    return bind_chunk_response_v2(envelope=response_text, payload=payload)
