"""Transport-layer envelope for AgentReview v2 semantic content (#200-A,
child of the distribution epic #199). Wiring this into a real transport
(offline file / Agent Router) is #200-C's job (a later PR in this DAG);
this module only freezes the contract and the fail-closed echo check.

## The problem this solves (D2)

``payload_sha256`` (``contracts_v2.ChunkPayloadV2``) proves a response
BELONGS to a chunk. It does not prove the response was produced over ONE
SPECIFIC, redacted ``review_content_v2.ReviewContentV2`` sidecar rather than
some other, equally ``payload_sha256``-matching one built for the same
chunk -- two sidecars for the same chunk are otherwise indistinguishable to
``consumer_v2.validate_response_binding_v2``.

``ChunkResponseEnvelopeValueV2`` (``contracts_v2.py:783-786``) is a closed
(``extra=\"forbid\"``), already-frozen, already-published, discriminated
``oneOf`` union with no field reserved for a content hash. Extending it
would change what every already-pinned ``v0.21.0`` consumer of that exact
union accepts -- a stop condition this plan explicitly refuses to cross
(see the #199 execution plan's D2 and its "Stop" clause for #200-A).

## The fix: wrap, never touch

This module defines a NEW, independently versioned transport envelope
(``agent-review.review-transport-envelope.v1``, ``schema_version: 1`` --
its own lineage, unrelated to the v2 chunk-response schema version) that
carries the UNMODIFIED ``ChunkResponseEnvelopeValueV2`` as one field
(``response``) plus an echo of the two hashes
(``request_sha256``/``content_sha256``) the far end must return verbatim.
``verify_transport_echo_v1`` is the fail-closed gate a caller MUST pass
BEFORE the inner ``response`` is ever handed to ``consumer_v2.
bind_chunk_response_v2``: a syntactically perfect, correctly
``payload_sha256``-bound response that was actually produced against the
WRONG content is rejected here, before it reaches the v2 binding authority
at all. Not one byte of ``ChunkResponseEnvelopeValueV2`` or its own hash
preimage (``contracts_v2.canonical_response_envelope_bytes_v2``) is touched
to make this possible.

## What this module does NOT do

- call any transport (offline file, HTTP, or otherwise) -- #200-C;
- decide retries, timeouts, or how a missing/unparseable response maps to
  ``TRANSPORT_FAILURE_REASON_V2`` -- #200-C, reusing
  ``consumer_v2.TRANSPORT_FAILURE_REASON_V2`` unmodified;
- persist a request or response to disk -- #200-C, and even then only
  hashes, never raw content (this contract does not even have a field for
  raw provider request/response bytes -- ``response`` is a validated,
  already-sanitized ``ChunkResponseEnvelopeValueV2``, not a raw string).
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import model_validator

from app.agent_review.contracts_v2 import (
    ChunkResponseEnvelopeValueV2,
    ContractV2Model,
    GitSha,
    SafeIdentifier,
    Sha256,
    validate_chunk_response_envelope_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

REVIEW_TRANSPORT_ENVELOPE_SCHEMA_V1 = "agent-review.review-transport-envelope.v1"

CONTENT_ECHO_MISMATCH_REASON_V2 = "content_echo_mismatch"
REQUEST_ECHO_MISMATCH_REASON_V2 = "request_echo_mismatch"
TRANSPORT_ENVELOPE_INVALID_REASON_V2 = "transport_envelope_invalid"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, and review_content_v2.py -- not a new rule.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class TransportEchoError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by ``verify_transport_echo_v1``. Carries a stable
    ``reason_code`` only -- never request or response content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def compute_request_sha256_v2(
    *, run_id: str, chunk_id: str, head_sha: str, payload_sha256: str, content_sha256: str
) -> str:
    """The exact, fixed preimage for ``ChunkReviewRequestV2.request_sha256``:
    every field the far end must echo back to prove which exact request it
    answered, hashed TOGETHER so a partial echo (e.g. the right
    ``payload_sha256`` paired with the wrong ``content_sha256``) cannot
    pass ``verify_transport_echo_v1``."""

    material = {
        "run_id": run_id,
        "chunk_id": chunk_id,
        "head_sha": head_sha,
        "payload_sha256": payload_sha256,
        "content_sha256": content_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes_v2(material)).hexdigest()


class ChunkReviewRequestV2(ContractV2Model):
    """The outbound request's identity -- never the raw reviewable content
    itself (that stays inside ``review_content_v2.ReviewContentV2``,
    resolved by ``content_sha256`` reference only, never re-serialized here
    as a bare, unauthenticated blob a transport implementation could swap)."""

    run_id: Sha256
    chunk_id: SafeIdentifier
    head_sha: GitSha
    payload_sha256: Sha256
    content_sha256: Sha256
    request_sha256: Sha256

    @model_validator(mode="after")
    def validate_request_hash(self) -> ChunkReviewRequestV2:
        expected = compute_request_sha256_v2(
            run_id=self.run_id,
            chunk_id=self.chunk_id,
            head_sha=self.head_sha,
            payload_sha256=self.payload_sha256,
            content_sha256=self.content_sha256,
        )
        if self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match the canonical request material")
        return self


class ChunkReviewTransportEnvelopeV1(ContractV2Model):
    """The wire-level wrapper: an echo of both integrity hashes, plus the
    UNMODIFIED, independently re-validated v2 response envelope.

    ``response`` is typed as the EXACT SAME ``ChunkResponseEnvelopeValueV2``
    discriminated union ``contracts_v2`` already defines and validates --
    not re-declared, not loosened, not given a new optional field. Wrapping
    it here does not change one byte of what that union accepts or how
    ``consumer_v2.bind_chunk_response_v2`` later validates it."""

    schema_id: Literal["agent-review.review-transport-envelope.v1"]
    schema_version: Literal[1]
    request_sha256: Sha256
    content_sha256: Sha256
    response: ChunkResponseEnvelopeValueV2


def verify_transport_echo_v1(
    envelope: ChunkReviewTransportEnvelopeV1, *, request: ChunkReviewRequestV2
) -> ChunkResponseEnvelopeValueV2:
    """The fail-closed gate a caller MUST pass before ``envelope.response``
    is handed to ``consumer_v2.bind_chunk_response_v2``. Returns the
    freshly, independently revalidated inner response envelope only on
    success (never the caller's original reference -- mirrors
    ``consumer_v2.bind_chunk_response_v2``'s own "fresh copy, not the
    caller's object" discipline); raises ``TransportEchoError`` on any
    divergence. Never returns a partially verified value: either both
    hashes echo exactly and the inner envelope independently validates, or
    an exception propagates.

    Checks the inner envelope's OWN validity first: an ``envelope.response``
    that fails ``validate_chunk_response_envelope_v2`` on its own terms is
    ``transport_envelope_invalid`` regardless of whether the outer hashes
    happen to echo correctly -- there is no reason to trust a hash echo
    wrapped around content that is not even a valid v2 envelope."""

    try:
        inner = validate_chunk_response_envelope_v2(envelope.response)
    except Exception as exc:
        raise TransportEchoError(TRANSPORT_ENVELOPE_INVALID_REASON_V2) from exc

    if envelope.request_sha256 != request.request_sha256:
        raise TransportEchoError(REQUEST_ECHO_MISMATCH_REASON_V2)
    if envelope.content_sha256 != request.content_sha256:
        raise TransportEchoError(CONTENT_ECHO_MISMATCH_REASON_V2)

    return inner
