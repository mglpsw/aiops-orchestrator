"""Strict, fail-closed JSON parsing and deterministic digest primitives.

Promoted verbatim (behaviour-for-behaviour, including exception messages) from
``app.caem_consumer.f0``'s module-private helpers, which have been the de-facto
reference implementation of this discipline in the repository. ``f0`` now
delegates to this module; its private names remain as thin aliases so that
existing importers -- including ``tests/caem_consumer/test_git_projection.py``,
which imports ``_canonical_json_digest``/``_git_blob_oid`` directly -- keep
working and, critically, keep producing byte-identical output.

## Why this module exists

`#201-C0`'s provenance sidecar and base-owned policy loader need exactly this
discipline: reject duplicate object keys, reject non-finite numbers, and hash a
canonical serialisation. Writing a fourth copy would mean four places to get
fail-closed parsing subtly wrong. (Three already exist: ``f0``,
``app.agent_review.trusted_check_supervisor_v2`` and ``app.ri_b0a.reuse_manifest``.
Only ``f0`` is consolidated here -- the supervisor is `#201-B3` protocol code
whose parsing is adversarially pinned, and ``reuse_manifest`` belongs to the
Review Intelligence track, which `#201-C0` must not absorb. Both are recorded
as deliberate non-changes, not oversights.)

## Two digest families, deliberately

``sha256:``-prefixed helpers reproduce CAEM's ``_DIGEST_RE`` form and exist for
``f0``'s benefit. Bare-hex helpers reproduce AgentReview's own ``Sha256 =
^[0-9a-f]{64}$`` form (``app/agent_review/contracts_v2.py``). The divergence is
a decision, not an accident: neither family may be silently converted into the
other, because both are already frozen in published artifacts.

## What this module must never do

Grow a domain rule. It parses and hashes; it does not know what a check, a
contract, a pin or a run is, and it never decides whether something is
authoritative.
"""

from __future__ import annotations

import hashlib
import json

__all__ = [
    "DIGEST_PREFIX",
    "canonical_json_bytes",
    "canonical_json_digest_hex",
    "canonical_json_text",
    "git_blob_oid",
    "prefixed_canonical_json_digest",
    "prefixed_raw_bytes_digest",
    "raw_bytes_digest_hex",
    "reject_duplicate_keys",
    "reject_non_finite",
    "strict_json_loads",
]

DIGEST_PREFIX = "sha256:"


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """``object_pairs_hook`` that refuses a repeated key instead of letting the
    last occurrence silently win.

    A document whose meaning depends on which duplicate the parser happened to
    keep is not a document anyone can verify, so it is rejected outright rather
    than normalised."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"DUPLICATE_JSON_KEY: {key}")
        result[key] = value
    return result


def reject_non_finite(value: str) -> None:
    """``parse_constant`` hook: ``NaN``/``Infinity``/``-Infinity`` are not
    representable in canonical JSON, so they can never round-trip to a stable
    digest. Refused rather than coerced."""

    raise ValueError(f"CANONICAL_JSON_INVALID: non-finite number {value}")


def strict_json_loads(data: bytes | str) -> object:
    """Parse JSON with duplicate-key and non-finite-number rejection.

    Raises ``ValueError`` on any violation -- never returns a
    partially-trusted document."""

    return json.loads(data, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_non_finite)


def canonical_json_text(value: object) -> str:
    """The one canonicalisation this repository uses for semantic digests:
    sorted keys, no whitespace, Unicode left unescaped.

    `allow_nan=False` on purpose: `json.dumps` defaults to emitting the
    non-standard tokens `NaN`/`Infinity`/`-Infinity`, which `strict_json_loads`
    itself refuses to read back. Without this, a value containing one of them
    would get a stable, well-formed-looking digest from the same module that
    refuses to parse the text producing it -- canonicalisation and parsing
    must agree on what counts as valid JSON."""

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_json_bytes(value: object) -> bytes:
    return canonical_json_text(value).encode("utf-8")


def canonical_json_digest_hex(value: object) -> str:
    """Bare-hex canonical digest -- the AgentReview ``Sha256`` form."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def raw_bytes_digest_hex(data: bytes) -> str:
    """Bare-hex digest of raw bytes, with no JSON reinterpretation."""

    return hashlib.sha256(data).hexdigest()


def prefixed_canonical_json_digest(value: object) -> str:
    """``sha256:``-prefixed canonical digest -- the CAEM ``_DIGEST_RE`` form."""

    return DIGEST_PREFIX + canonical_json_digest_hex(value)


def prefixed_raw_bytes_digest(data: bytes) -> str:
    return DIGEST_PREFIX + raw_bytes_digest_hex(data)


def git_blob_oid(content: bytes) -> str:
    """Git's own blob object id (``sha1`` over ``blob <len>\\0`` + content).

    ``sha1`` here is not a security claim -- it is Git's object-naming format,
    reproduced so a projection can be compared against real ``git`` output."""

    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()  # noqa: S324 - git object id format, not a security digest
