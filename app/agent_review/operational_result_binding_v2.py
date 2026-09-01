"""`#200-F` authority D -- range-aware binding of untrusted model output.

## The predecessor P0

A model returns a finding with a **valid path**, in a **valid fragment's
file**, at a **line outside that fragment's range**. This is not an attack; an
off-by-N line number is among the most ordinary things a language model does.

In `#276`::

    binder      -> accepts (it checks file paths and contract ids, never ranges)
    synthesis   -> raises, much later
    whole run   -> aborts

Worse, `#276` shipped a written justification claiming the binder refused
out-of-scope findings. It does not. ``_validate_chunk_review_result_scope_v2``
compares ``finding_files <= payload_files`` and contract ids; the range check
lives in ``chunk_result_scope_v2``, which runs at scope revalidation *after*
binding. The claim was false and the control that recorded it passed anyway.

## The invariant

::

    untrusted result
      -> path validated
      -> fragment identity validated
      -> line/range validated inside that fragment
      -> ONLY THEN may a BoundChunkResponseV2 exist

An ordinary out-of-range line becomes a typed *binding* refusal for that
chunk, discovered where the untrusted material enters, not a late explosion
that takes the whole run with it.

## Why this lives beside ``consumer_v2`` rather than inside it

Fragment ranges are manifest material. ``ChunkPayloadV2`` carries file-level
coverage and references but no line ranges, and adding them would change a
*published* schema. Threading the manifest through
``bind_chunk_response_v2`` would change a public signature every existing
caller depends on.

The composer is the one place that holds the manifest and the responses at the
same time, so the range authority belongs at that seam. Both binders are
covered here -- offline envelope and Router receipt -- so neither transport
can acquire a weaker definition of scope than the other, which is the property
``_validate_chunk_review_result_scope_v2`` was written to protect and which
this module extends rather than forks.

The range predicate itself is **imported** from ``chunk_result_scope_v2``
rather than reimplemented. Two range checks that agreed today would drift, and
a binder disagreeing with synthesis about what "inside the fragment" means
would recreate the defect in mirror image.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agent_review._router_receipt_v2 import _VerifiedRouterResultV2
from app.agent_review.chunk_result_scope_v2 import (
    FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2,
    UNKNOWN_CHUNK_RESULT_REASON_V2,
    _finding_within_chunk_fragments_v2,
)
from app.agent_review.consumer_v2 import (
    BoundChunkResponseV2,
    bind_chunk_response_v2,
    bind_verified_router_result_v2,
)
from app.agent_review.contracts_v2 import (
    RESPONSE_CONTRACT_INVALID_REASON_V2,
    ChunkResponseEnvelopeValueV2,
    ChunkResponseSuccessEnvelopeV2,
    ChunkPayloadV2,
    ChunkReviewResultV2,
    ResponseBindingError,
    validate_chunk_response_envelope_v2,
)
from app.agent_review.manifest_v2 import ManifestV2

__all__ = [
    "FINDING_PATH_OUTSIDE_CHUNK_FRAGMENTS_REASON_V2",
    "bind_offline_response_with_range_authority_v2",
    "bind_router_result_with_range_authority_v2",
    "validate_result_fragment_ranges_v2",
]


FINDING_PATH_OUTSIDE_CHUNK_FRAGMENTS_REASON_V2 = "finding_path_outside_chunk_fragments"


def validate_result_fragment_ranges_v2(
    *,
    result: ChunkReviewResultV2,
    chunk_id: str,
    manifest: ManifestV2,
) -> None:
    """Validate path, fragment identity and range for every finding.

    Raises ``ResponseBindingError`` -- the binding layer's own refusal type,
    already a member of the operational refusal family -- so an out-of-range
    line reads as "this response could not be bound", which is exactly what
    happened, rather than as a synthesis failure.
    """
    chunks_by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}
    chunk = chunks_by_id.get(chunk_id)
    if chunk is None:
        raise ResponseBindingError(UNKNOWN_CHUNK_RESULT_REASON_V2)

    fragments_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
    # A manifest whose chunks reference fragments it does not carry is
    # internally inconsistent. That is our defect, not the model's, so it is
    # not laundered into a binding refusal.
    chunk_fragment_ids = list(chunk.fragment_ids)
    chunk_paths = {fragments_by_id[fragment_id].path for fragment_id in chunk_fragment_ids}

    for finding in result.findings:
        if finding.file_path not in chunk_paths:
            # Distinct from the range case on purpose: "you reviewed a file
            # that is not in this chunk" and "you cited a line outside the
            # hunk" are different mistakes and an operator triaging model
            # behaviour needs to tell them apart.
            raise ResponseBindingError(FINDING_PATH_OUTSIDE_CHUNK_FRAGMENTS_REASON_V2)

        if not _finding_within_chunk_fragments_v2(
            fragments_by_id=fragments_by_id,
            chunk_fragment_ids=chunk_fragment_ids,
            finding=finding,
        ):
            raise ResponseBindingError(FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2)


def bind_offline_response_with_range_authority_v2(
    *,
    envelope: ChunkResponseEnvelopeValueV2 | Mapping[str, Any] | str | bytes,
    payload: ChunkPayloadV2,
    manifest: ManifestV2,
) -> BoundChunkResponseV2:
    """Offline path: ranges validated before any bound response exists.

    The envelope is validated here and the resulting **model** -- not the
    caller's original object -- is what both the range check and the binder
    see. That closes the window in which a mutable mapping could present
    different bytes to each reader.

    Precision, after review: the model is validated *twice*, because
    ``bind_chunk_response_v2`` re-validates whatever it is handed. An earlier
    revision of this docstring said "once", which was wrong. The security
    property is unaffected -- the second validation receives a frozen model,
    so both readers see identical bytes -- but the sentence was load-bearing
    about this seam and is corrected rather than quietly left.
    """
    try:
        fresh_envelope = validate_chunk_response_envelope_v2(envelope)
    except Exception as exc:  # noqa: BLE001 -- normalised to a typed refusal
        raise ResponseBindingError(RESPONSE_CONTRACT_INVALID_REASON_V2) from exc

    # An error envelope carries no findings and is refused by the binder with
    # its own structured reason. Range checking it would be meaningless, and
    # inventing a range refusal here would mask the real cause.
    if isinstance(fresh_envelope, ChunkResponseSuccessEnvelopeV2):
        validate_result_fragment_ranges_v2(
            result=fresh_envelope.result,
            chunk_id=fresh_envelope.chunk_id,
            manifest=manifest,
        )

    return bind_chunk_response_v2(envelope=fresh_envelope, payload=payload)


def bind_router_result_with_range_authority_v2(
    *,
    verified: _VerifiedRouterResultV2,
    payload: ChunkPayloadV2,
    manifest: ManifestV2,
) -> BoundChunkResponseV2:
    """Router path: the same authority, so neither transport is weaker.

    Receipt verification proves the Router-side input/execution/output
    relations. It says nothing about whether the model cited a line inside the
    hunk it was shown, so a receipt-verified result gets exactly the same
    range scrutiny as an offline one.
    """
    if not isinstance(verified, _VerifiedRouterResultV2):
        raise ResponseBindingError(RESPONSE_CONTRACT_INVALID_REASON_V2)

    validate_result_fragment_ranges_v2(
        result=verified._result,
        chunk_id=verified._request.chunk_id,
        manifest=manifest,
    )

    return bind_verified_router_result_v2(verified=verified, payload=payload)
