"""Golden vectors for `app.common.strict_json` (`#201-C0`, C0-1).

The point of these tests is NOT that the shared module "works" -- it is that
promoting `app.caem_consumer.f0`'s private helpers into a shared module moved
zero bytes. Every expected value below is a literal, computed from the
implementation that existed BEFORE the promotion. If a future refactor of
`strict_json` changes any of them, a pinned CAEM 3.0 F0 digest changes with it,
and the pin verification breaks -- so these literals are the tripwire that
fires first, with a clearer message than a pin mismatch.
"""

from __future__ import annotations

import pytest

from app.caem_consumer import f0
from app.common.strict_json import (
    DIGEST_PREFIX,
    canonical_json_digest_hex,
    canonical_json_text,
    git_blob_oid,
    prefixed_canonical_json_digest,
    prefixed_raw_bytes_digest,
    raw_bytes_digest_hex,
    reject_duplicate_keys,
    reject_non_finite,
    strict_json_loads,
)

# -- golden vectors -----------------------------------------------------------
# Literals, not recomputations. `sha256("{}")` and friends, fixed here so the
# test cannot drift along with the implementation it is meant to pin.

EMPTY_OBJECT_CANONICAL_DIGEST = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
EMPTY_BYTES_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
UNICODE_CANONICAL_TEXT = '{"a":"é","b":1}'


def test_canonical_json_text_sorts_keys_and_leaves_unicode_unescaped() -> None:
    assert canonical_json_text({"b": 1, "a": "é"}) == UNICODE_CANONICAL_TEXT


def test_canonical_json_digest_matches_the_frozen_empty_object_vector() -> None:
    assert canonical_json_digest_hex({}) == EMPTY_OBJECT_CANONICAL_DIGEST


def test_raw_bytes_digest_matches_the_frozen_empty_input_vector() -> None:
    assert raw_bytes_digest_hex(b"") == EMPTY_BYTES_DIGEST


def test_prefixed_helpers_add_exactly_the_caem_digest_prefix() -> None:
    assert prefixed_canonical_json_digest({}) == DIGEST_PREFIX + EMPTY_OBJECT_CANONICAL_DIGEST
    assert prefixed_raw_bytes_digest(b"") == DIGEST_PREFIX + EMPTY_BYTES_DIGEST


def test_git_blob_oid_reproduces_real_git_hash_object_output() -> None:
    # `printf '' | git hash-object --stdin` and `printf 'hello\n' | git hash-object --stdin`.
    assert git_blob_oid(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_oid(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


# -- the promotion moved nothing ----------------------------------------------


def test_f0_private_names_are_aliases_of_the_shared_implementation() -> None:
    """Identity, not equality: if `f0` ever grows a second implementation
    again, this fails immediately rather than at digest-comparison time."""

    assert f0._canonical_json_digest is prefixed_canonical_json_digest
    assert f0._raw_bytes_digest is prefixed_raw_bytes_digest
    assert f0._git_blob_oid is git_blob_oid
    assert f0._duplicate_keys is reject_duplicate_keys
    assert f0._reject_non_finite is reject_non_finite
    assert f0._strict_json_loads is strict_json_loads


def test_f0_digest_helpers_still_emit_the_caem_prefixed_form() -> None:
    """`f0` consumes the `sha256:`-prefixed family; AgentReview consumes bare
    hex. Neither may be silently converted into the other."""

    assert f0._canonical_json_digest({}).startswith(DIGEST_PREFIX)
    assert f0._raw_bytes_digest(b"").startswith(DIGEST_PREFIX)
    assert not canonical_json_digest_hex({}).startswith(DIGEST_PREFIX)


# -- fail-closed parsing ------------------------------------------------------


def test_duplicate_keys_are_refused_with_the_frozen_message() -> None:
    with pytest.raises(ValueError, match=r"^DUPLICATE_JSON_KEY: a$"):
        strict_json_loads('{"a": 1, "a": 2}')


def test_last_duplicate_does_not_silently_win() -> None:
    with pytest.raises(ValueError):
        strict_json_loads('{"a": 1, "a": 1}')


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_refused_with_the_frozen_message(literal: str) -> None:
    with pytest.raises(ValueError, match=r"^CANONICAL_JSON_INVALID: non-finite number "):
        strict_json_loads(f'{{"value": {literal}}}')


def test_nested_duplicate_keys_are_refused_too() -> None:
    with pytest.raises(ValueError, match=r"^DUPLICATE_JSON_KEY: inner$"):
        strict_json_loads('{"outer": {"inner": 1, "inner": 2}}')


def test_malformed_json_still_raises_rather_than_returning_partial_data() -> None:
    with pytest.raises(ValueError):
        strict_json_loads('{"truncated": ')


def test_bytes_and_str_inputs_agree() -> None:
    assert strict_json_loads(b'{"a": 1}') == strict_json_loads('{"a": 1}') == {"a": 1}
