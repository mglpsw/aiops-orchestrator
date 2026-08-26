"""Explicit v1/v2 contract-version selection for AgentReview.

Selection never infers a version from partial field presence or a shape
heuristic.  It compares each raw object's own complete, required version
markers -- the exact ``schema_id``/``schema_version`` fields each contract
already declares as its canonical identity -- against the literal constants
each contract module owns (``contracts_v2`` for v2, ``schemas`` for v1).  A
version is accepted only when the caller's explicit request, the payload's
markers, and (when supplied) the response's markers all agree exactly.

This module never re-implements payload/response binding.  It only decides,
before any v2 object is constructed, whether a call site is allowed to
proceed with the v2 pipeline at all.  Every response-bearing call site --
the offline envelope path, the quality gate CLI and the Agent Router wire --
consults this one selector; none may re-derive a version locally.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from app.agent_review.contracts_v2 import (
    CHUNK_PAYLOAD_SCHEMA_V2,
    CHUNK_RESPONSE_ENVELOPE_SCHEMA_V2,
    CHUNK_RESPONSE_SCHEMA_V2,
    ResponseBindingError,
)
from app.agent_review.schemas import CHUNK_PAYLOAD_SCHEMA as CHUNK_PAYLOAD_SCHEMA_V1

UNSUPPORTED_CONTRACT_VERSION_REASON_V2 = "unsupported_contract_version"
MIXED_CONTRACT_VERSIONS_REASON_V2 = "mixed_contract_versions"

# The v2 line has two legitimate response artifacts, not one:
#
# * ``chunk-response-envelope.v2`` -- the offline/synthetic transport
#   envelope this module has always recognised;
# * ``chunk-response.v2`` -- ``ChunkReviewResultV2``, the semantic document
#   the Agent Router returns as assistant content (`#200-C-WIRE`).
#
# They are different artifacts carrying the same contract *version*. Both
# must resolve to ``ContractVersionV2.V2`` through this one authority; a
# Router-local shape check would be a second selector.
_RESPONSE_SCHEMA_IDS_V2 = frozenset(
    {CHUNK_RESPONSE_ENVELOPE_SCHEMA_V2, CHUNK_RESPONSE_SCHEMA_V2}
)

_PAYLOAD_SCHEMA_VERSION_V1 = 1
_PAYLOAD_SCHEMA_VERSION_V2 = 2
_RESPONSE_SCHEMA_VERSION_V1 = 1
_RESPONSE_SCHEMA_VERSION_V2 = 2


class ContractVersionV2(str, Enum):
    """The two contract lines this repository operates today.

    ``V1`` selects the legacy, untouched pipeline (chunk_result_parser.py
    and friends). ``V2`` selects the verified-binding pipeline
    (consumer_v2.py / parser_v2.py). There is no ``unknown``/``mixed``
    member: those are rejections (``ResponseBindingError``), never values.
    """

    V1 = "v1"
    V2 = "v2"


def _payload_contract_version(payload_raw: Mapping[str, object]) -> ContractVersionV2 | None:
    schema_id = payload_raw.get("schema_id")
    schema_version = payload_raw.get("schema_version")
    if schema_id == CHUNK_PAYLOAD_SCHEMA_V2 and schema_version == _PAYLOAD_SCHEMA_VERSION_V2:
        return ContractVersionV2.V2
    if schema_id == CHUNK_PAYLOAD_SCHEMA_V1 and schema_version == _PAYLOAD_SCHEMA_VERSION_V1:
        return ContractVersionV2.V1
    return None


def _response_contract_version(response_raw: Mapping[str, object]) -> ContractVersionV2 | None:
    schema_id = response_raw.get("schema_id")
    schema_version = response_raw.get("schema_version")
    if (
        isinstance(schema_id, str)
        and schema_id in _RESPONSE_SCHEMA_IDS_V2
        and schema_version == _RESPONSE_SCHEMA_VERSION_V2
    ):
        return ContractVersionV2.V2
    # v1's ChunkResponse (schemas.py) never declares a schema_id field at
    # all; schema_version == 1 alone is its genuine, complete canonical
    # marker -- not a partial-field guess. The key must be *absent*, not
    # merely falsy: {"schema_id": null, ...} is a corrupted/foreign
    # document that happens to share Python's `None`, not a v1 response,
    # and must not be silently accepted as one.
    if "schema_id" not in response_raw and schema_version == _RESPONSE_SCHEMA_VERSION_V1:
        return ContractVersionV2.V1
    return None


def select_contract_version(
    *,
    requested: str | None,
    payload_raw: Mapping[str, object],
    response_raw: Mapping[str, object] | None = None,
) -> ContractVersionV2:
    """Authoritative, explicit version gate.

    Raises ``ResponseBindingError`` (reusing the existing binding exception
    type, never a new hierarchy):

    * ``unsupported_contract_version`` -- ``requested`` is not exactly
      ``"v1"``/``"v2"``, or a raw object's own markers do not correspond to
      any known contract version at all;
    * ``mixed_contract_versions`` -- the request, the payload, and (when
      supplied) the response each identify a *known* version, but they do
      not all agree with each other.
    """

    if requested not in (ContractVersionV2.V1.value, ContractVersionV2.V2.value):
        raise ResponseBindingError(UNSUPPORTED_CONTRACT_VERSION_REASON_V2)
    requested_version = ContractVersionV2(requested)

    payload_version = _payload_contract_version(payload_raw)
    if payload_version is None:
        raise ResponseBindingError(UNSUPPORTED_CONTRACT_VERSION_REASON_V2)

    response_version: ContractVersionV2 | None = None
    if response_raw is not None:
        response_version = _response_contract_version(response_raw)
        if response_version is None:
            raise ResponseBindingError(UNSUPPORTED_CONTRACT_VERSION_REASON_V2)

    versions = {requested_version, payload_version}
    if response_version is not None:
        versions.add(response_version)
    if len(versions) > 1:
        raise ResponseBindingError(MIXED_CONTRACT_VERSIONS_REASON_V2)

    return requested_version
