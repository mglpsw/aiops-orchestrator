"""F2 -- emit and cross-validate PayloadSetV2 against a manifest and real
chunk payloads (issue #132, child of tracker #110).

``payload_set_v2.py`` (#105) already defines the contract, its canonical
hash, and ``bind_payload_set_to_manifest_v2`` (``run_id``/``manifest_hash``/
exact ``chunk_id`` set against a ``ManifestV2``). What #105 could not do --
by its own module docstring, "emitting a ``PayloadSetV2`` from a real run"
is explicitly this issue's job -- is cross-validate a ``PayloadSetV2``
against the REAL ``ChunkPayloadV2`` objects it claims to attest, not merely
their ``chunk_id``s. This module adds exactly that, and the emission
function that produces a ``PayloadSetV2`` honestly, from real payloads.

## The full cross-validation chain

``bind_payload_set_to_payloads_v2`` never re-derives a check #105 already
owns -- it calls ``bind_payload_set_to_manifest_v2`` first (unmodified),
then adds the checks that require the ACTUAL payload objects, which
#105 never had:

1. ``run_id``/``manifest_hash``/exact ``chunk_id`` SET match -- delegated,
   not reimplemented (#105's ``bind_payload_set_to_manifest_v2``).
2. Every entry's ``chunk_id`` resolves to a real payload -- an entry with
   no matching payload is an error
   (``PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2``).
3. Every supplied payload's ``chunk_id`` is named by some entry -- an
   extra, unattested payload is an error
   (``PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2``).
4. Each resolved payload is revalidated with ``contracts_v2.verify_payload_sha256_v2``
   (reused, not reimplemented) -- a payload that bypassed its own
   construction validator (e.g. via ``model_copy``) is caught here, not
   silently trusted (``PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2``).
5. ``payload.payload_sha256 == entry.payload_sha256`` --
   ``PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2``.
6. ``payload.run_id``, ``payload.chunk_id``, and
   ``payload.identity.manifest_hash`` are each individually coherent with
   the manifest and the entry they are being matched against --
   ``PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2``/
   ``PAYLOAD_SET_PAYLOAD_CHUNK_ID_INCOHERENT_REASON_V2``/
   ``PAYLOAD_SET_PAYLOAD_MANIFEST_HASH_INCOHERENT_REASON_V2``.

Every clause raises a distinct, stable reason code; none is a warning.

**``run_id`` and ``manifest_hash`` incoherence are both genuinely
reachable, independent lines of defense -- confirmed by mutation testing,
not assumed.** ``plan_lossless_chunks_v2``'s default ``chunk_id`` numbering
(``chunk-0000``, ``chunk-0001``, ...) is NOT globally unique across
different runs -- two independent single-chunk manifests built the same
way produce the IDENTICAL ``chunk_id``, confirmed directly. A payload that
is genuinely valid on its own (``verify_payload_sha256_v2`` passes) but was
built for a DIFFERENT run can therefore be smuggled into this payload set
via a coincidental ``chunk_id`` collision, with the entry's
``payload_sha256`` declared as that foreign payload's real hash -- clause 5
alone would pass. Temporarily disabling the ``run_id`` check while testing
this exact scenario showed the ``manifest_hash`` check independently
catches the same smuggled payload (the two different manifests' hashes
genuinely differ) -- so checking ``run_id`` first does not make
``manifest_hash`` redundant in general, only in THIS specific scenario;
both stay as independently meaningful, reachable checks, not merely
defense in depth for each other.

``chunk_id`` incoherence, by contrast, is provably unreachable through this
function's own call path, kept as defense in depth (mirroring
``readiness_decision_v2.py``'s and ``run_assembly_v2.py``'s identical
precedent): ``_payloads_by_chunk_id`` keys every payload by its OWN
``chunk_id`` field, so a successful ``payloads_by_chunk_id[entry.chunk_id]``
lookup already guarantees the resolved payload's ``chunk_id`` equals
``entry.chunk_id`` by construction.

``emit_payload_set_v2`` builds a real ``PayloadSetV2`` from a manifest and
its actually-built ``ChunkPayloadV2`` objects, then immediately runs it
through ``bind_payload_set_to_payloads_v2`` before returning -- a producer
that (through a bug) built a tampered or incoherent payload must not
silently mint a set that certifies it as good.

Deliberately out of scope, per the issue: readiness; conformance
dual-target; any change to ``PayloadSetV2``'s own contract (frozen by
#105 -- a real need to change it is a stop condition, not a local edit).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from app.agent_review.contracts_v2 import ChunkPayloadV2, verify_payload_sha256_v2
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.payload_set_v2 import (
    PAYLOAD_SET_SCHEMA_V2,
    PayloadSetBindingError,
    PayloadSetEntryV2,
    PayloadSetV2,
    bind_payload_set_to_manifest_v2,
    compute_payload_set_sha256_v2,
    verify_payload_set_sha256_v2,
)

PAYLOAD_SET_HASH_TAMPERED_REASON_V2 = "payload_set_hash_tampered"
PAYLOAD_SET_DUPLICATE_PAYLOAD_REASON_V2 = "payload_set_duplicate_payload"
PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2 = "payload_set_entry_missing_payload"
PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2 = "payload_set_extra_payload"
PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2 = "payload_set_payload_tampered"
PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2 = "payload_set_entry_payload_hash_mismatch"
PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2 = "payload_set_payload_run_id_incoherent"
PAYLOAD_SET_PAYLOAD_CHUNK_ID_INCOHERENT_REASON_V2 = "payload_set_payload_chunk_id_incoherent"
PAYLOAD_SET_PAYLOAD_MANIFEST_HASH_INCOHERENT_REASON_V2 = "payload_set_payload_manifest_hash_incoherent"

_PAYLOAD_SET_SOURCE_V2 = "aiops-review-build-payload-set-v2"


# The payload set could not satisfy its own contract from otherwise-valid
# caller material.
PAYLOAD_SET_CONTRACT_INVALID_REASON_V2 = "payload_set_contract_invalid"


def _payloads_by_chunk_id(payloads: Sequence[ChunkPayloadV2]) -> dict[str, ChunkPayloadV2]:
    by_chunk_id: dict[str, ChunkPayloadV2] = {}
    for payload in payloads:
        if payload.chunk_id in by_chunk_id:
            raise PayloadSetBindingError(PAYLOAD_SET_DUPLICATE_PAYLOAD_REASON_V2)
        by_chunk_id[payload.chunk_id] = payload
    return by_chunk_id


def bind_payload_set_to_payloads_v2(
    payload_set: PayloadSetV2, payloads: Sequence[ChunkPayloadV2], manifest: ManifestV2
) -> None:
    """Full cross-validation: ``payload_set`` <-> ``manifest`` <-> the REAL
    ``payloads`` it attests. See the module docstring for the exact
    six-clause chain and each clause's reason code.

    As a FINAL check, after every structural clause below has already
    passed, the payload set's OWN attestation hash is revalidated via
    ``verify_payload_set_sha256_v2`` -- a ``PayloadSetV2`` is, like
    ``ChunkPayloadV2``, a freely constructible plain object with no seal
    proving it was built through its own constructor's hash validator
    (e.g. a caller could hand in ``payload_set.model_copy(update=
    {"payload_set_sha256": "0" * 64})``). This check runs LAST, not
    first: `run_id`/`manifest_hash` tampering already invalidates the
    set's own hash too (both fields are part of its preimage), so
    checking the hash first would mask the more specific
    ``PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2``/
    ``PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2`` reason codes
    ``bind_payload_set_to_manifest_v2`` already provides for those exact
    cases (confirmed by reproduction: an earlier revision of this fix
    checked the hash first and broke those two pre-existing regressions).
    Placed last, this check is reached only when every other clause
    already agrees the set matches its manifest and payloads -- exactly
    the residual case a Codex review of #144 found uncovered: the
    attestation hash itself forged while everything else still lines up.
    A tampered set is rejected with ``PAYLOAD_SET_HASH_TAMPERED_REASON_V2``."""

    bind_payload_set_to_manifest_v2(payload_set, manifest)

    payloads_by_chunk_id = _payloads_by_chunk_id(payloads)
    entry_chunk_ids = {entry.chunk_id for entry in payload_set.payloads}
    payload_chunk_ids = set(payloads_by_chunk_id)

    if entry_chunk_ids - payload_chunk_ids:
        raise PayloadSetBindingError(PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2)
    if payload_chunk_ids - entry_chunk_ids:
        raise PayloadSetBindingError(PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2)

    for entry in payload_set.payloads:
        payload = payloads_by_chunk_id[entry.chunk_id]
        try:
            verify_payload_sha256_v2(payload)
        except ValueError as exc:
            # `#200-D` predecessor, the OTHER direction: this was
            # `except Exception`, so a `TypeError`/`AttributeError` from a
            # defect INSIDE the verifier would have been reported to an
            # operator as a tampered payload. Those now crash.
            #
            # Stated precisely, because the pair `(ValidationError,
            # ValueError)` read like a narrowing it was not: pydantic's
            # `ValidationError` and `PydanticSerializationError` both subclass
            # `ValueError`, so this IS `except ValueError`. It therefore still
            # cannot distinguish a caller's tampered payload from a defect
            # that happens to raise `ValueError` -- see the "not converging"
            # section of the `#200-D` checkpoint.
            raise PayloadSetBindingError(PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2) from exc
        if payload.payload_sha256 != entry.payload_sha256:
            raise PayloadSetBindingError(PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2)
        if payload.run_id != manifest.run_id:
            raise PayloadSetBindingError(PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2)
        if payload.chunk_id != entry.chunk_id:
            raise PayloadSetBindingError(PAYLOAD_SET_PAYLOAD_CHUNK_ID_INCOHERENT_REASON_V2)
        if payload.identity.manifest_hash != manifest.identity.manifest_hash:
            raise PayloadSetBindingError(PAYLOAD_SET_PAYLOAD_MANIFEST_HASH_INCOHERENT_REASON_V2)

    try:
        verify_payload_set_sha256_v2(payload_set)
    except ValueError as exc:
        # same reason, and the same unresolved limitation, as the per-payload
        # verifier above
        raise PayloadSetBindingError(PAYLOAD_SET_HASH_TAMPERED_REASON_V2) from exc


def emit_payload_set_v2(manifest: ManifestV2, payloads: Sequence[ChunkPayloadV2]) -> PayloadSetV2:
    """Emit a real ``PayloadSetV2`` from ``manifest`` and its actually-built
    ``ChunkPayloadV2`` objects.

    Entries are ordered by ``chunk_id`` at construction (canonical-order
    input is not required -- ``canonical_payload_set_bytes_v2`` already
    re-sorts before hashing regardless -- but building in that order here
    keeps the constructed object's own field order matching its hash
    preimage's canonical order, which is simply tidier, not a correctness
    requirement).

    Runs the full ``bind_payload_set_to_payloads_v2`` cross-validation
    before returning: a producer that (through a bug) built a tampered or
    incoherent payload must not silently mint a set that certifies it.

    Public boundary (`#200-D` predecessor): every EXPECTED failure is a
    ``PayloadSetBindingError``. Callers do not need to know that this
    authority constructs a pydantic model to do its work.
    """

    try:
        return _emit_payload_set_v2(manifest, payloads)
    except PayloadSetBindingError:
        raise
    except ValidationError as exc:
        # `PayloadSetV2` has its own contract (at least one entry, canonical
        # digests). Constructing it from otherwise-valid caller material can
        # therefore fail pydantic, which a consumer cannot name.
        raise PayloadSetBindingError(PAYLOAD_SET_CONTRACT_INVALID_REASON_V2) from exc


def _emit_payload_set_v2(
    manifest: ManifestV2, payloads: Sequence[ChunkPayloadV2]
) -> PayloadSetV2:
    payloads_by_chunk_id = _payloads_by_chunk_id(payloads)
    entries = [
        PayloadSetEntryV2(chunk_id=chunk_id, payload_sha256=payload.payload_sha256)
        for chunk_id, payload in sorted(payloads_by_chunk_id.items())
    ]

    material = {
        "schema_id": PAYLOAD_SET_SCHEMA_V2,
        "schema_version": 2,
        "source": _PAYLOAD_SET_SOURCE_V2,
        "run_id": manifest.run_id,
        "manifest_hash": manifest.identity.manifest_hash,
        "payloads": entries,
    }
    payload_set_sha256 = compute_payload_set_sha256_v2(
        {**material, "payloads": [entry.model_dump(mode="json") for entry in entries]}
    )
    payload_set = PayloadSetV2(**material, payload_set_sha256=payload_set_sha256)

    bind_payload_set_to_payloads_v2(payload_set, payloads, manifest)
    return payload_set
