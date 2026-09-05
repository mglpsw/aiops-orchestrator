"""The ingress layer, attacked through its inventory rather than its branches.

`#331`, SGAQ-S0B-A0. Two propositions:

    P1A_SEMANTIC_TYPE_CLOSURE
    P1B_SEMANTIC_INGRESS_CLOSURE

WHY THE ORACLE BELOW IS WRITTEN OUT BY HAND, AGAIN, AND DIFFERENTLY

The predecessor (PR #336) had a hand-written oracle over contract FIELDS, and it
worked: every field-universe drift it was built for was caught. P1 escaped
anyway, three times, because the thing that was never enumerated was the set of
*functions that admit values*. `_as_keyed_relation` was a normaliser outside the
discipline, and no oracle had a row for it.

So this file enumerates normalisers, not fields. `_EXPECTED_NORMALIZERS` is
written independently of the runtime catalog and reconciled against it, and
every entry carries the adversarial matrix its declared policy implies. A
normaliser without a coverage row is RED before anyone reviews it.

WHAT THE #336 WITNESSES BECOME HERE

The stopped witnesses -- a `str` subclass key, a `set` used as a pair, a
`Mapping` used as a pair, a lone surrogate -- are inputs to the general keyed
relation property, not special cases. If they were special cases we would be
patching witnesses for the fourth time.
"""

from __future__ import annotations

import dataclasses
import gc

import pytest

import app.agent_review.sgaq_semantic_ingress_v2 as ingress
from app.agent_review.sgaq_semantic_ingress_v2 import (
    BoundedIntRecordV2,
    CollectionSemanticsV2,
    DuplicatePolicyV2,
    EncodingPolicyV2,
    ExactTypePolicyV2,
    InputShapeV2,
    InterpreterRegistryV2,
    MemberKindV2,
    OrderingPolicyV2,
    OutputShapeV2,
    SemanticIngressError,
    SemanticRecordV2,
    TextValueRecordV2,
    concrete_record_types,
    normalize,
    normalizer_ids,
    normalizer_spec,
    select_normalizer,
)

# --------------------------------------------------------------------------
# the independent normaliser coverage oracle
# --------------------------------------------------------------------------

#: normalizer_id -> declared policy, written by hand. NOT read from the runtime
#: catalog: an oracle derived from the thing it checks agrees with any mistake
#: in it.
_EXPECTED_NORMALIZERS: dict[str, dict[str, object]] = {
    "exact_text": dict(
        input_shape=InputShapeV2.SCALAR, output=OutputShapeV2.SCALAR,
        exact=ExactTypePolicyV2.EXACT_STR, duplicates=DuplicatePolicyV2.NOT_APPLICABLE,
        ordering=OrderingPolicyV2.NOT_APPLICABLE,
        encoding=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED),
    "exact_int": dict(
        input_shape=InputShapeV2.SCALAR, output=OutputShapeV2.SCALAR,
        exact=ExactTypePolicyV2.EXACT_INT, duplicates=DuplicatePolicyV2.NOT_APPLICABLE,
        ordering=OrderingPolicyV2.NOT_APPLICABLE,
        encoding=EncodingPolicyV2.NOT_APPLICABLE),
    "exact_bool": dict(
        input_shape=InputShapeV2.SCALAR, output=OutputShapeV2.SCALAR,
        exact=ExactTypePolicyV2.EXACT_BOOL, duplicates=DuplicatePolicyV2.NOT_APPLICABLE,
        ordering=OrderingPolicyV2.NOT_APPLICABLE,
        encoding=EncodingPolicyV2.NOT_APPLICABLE),
    "exact_record": dict(
        input_shape=InputShapeV2.SCALAR, output=OutputShapeV2.SCALAR,
        exact=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
        duplicates=DuplicatePolicyV2.NOT_APPLICABLE,
        ordering=OrderingPolicyV2.NOT_APPLICABLE,
        encoding=EncodingPolicyV2.NOT_APPLICABLE),
    "string_set": dict(
        input_shape=InputShapeV2.ITERABLE_OF_SCALAR, output=OutputShapeV2.FROZEN_SET,
        exact=ExactTypePolicyV2.EXACT_STR, duplicates=DuplicatePolicyV2.ABSORB,
        ordering=OrderingPolicyV2.CANONICAL_SORT,
        encoding=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED),
    "ordered_unique_records": dict(
        input_shape=InputShapeV2.ITERABLE_OF_RECORD, output=OutputShapeV2.ORDERED_TUPLE,
        exact=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
        duplicates=DuplicatePolicyV2.REJECT,
        ordering=OrderingPolicyV2.CALLER_ORDER_IS_SEMANTIC,
        encoding=EncodingPolicyV2.NOT_APPLICABLE),
    "keyed_text_relation": dict(
        input_shape=InputShapeV2.MAPPING_OR_PAIRS, output=OutputShapeV2.KEYED_PAIRS,
        exact=ExactTypePolicyV2.EXACT_STR, duplicates=DuplicatePolicyV2.REJECT,
        ordering=OrderingPolicyV2.CANONICAL_SORT,
        encoding=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED),
    "keyed_set_relation": dict(
        input_shape=InputShapeV2.MAPPING_OR_PAIRS, output=OutputShapeV2.KEYED_PAIRS,
        exact=ExactTypePolicyV2.EXACT_STR, duplicates=DuplicatePolicyV2.REJECT,
        ordering=OrderingPolicyV2.CANONICAL_SORT,
        encoding=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED),
    "keyed_record_relation": dict(
        input_shape=InputShapeV2.MAPPING_OR_PAIRS, output=OutputShapeV2.KEYED_PAIRS,
        exact=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
        duplicates=DuplicatePolicyV2.REJECT,
        ordering=OrderingPolicyV2.CANONICAL_SORT,
        encoding=EncodingPolicyV2.NOT_APPLICABLE),
}

#: A legal value for every normaliser, so the adversarial matrix has a control.
_VALID_INPUT = {
    "exact_text": "text",
    "exact_int": 7,
    "exact_bool": True,
    "exact_record": TextValueRecordV2(name="n", value="v"),
    "string_set": ["a", "b"],
    "ordered_unique_records": (TextValueRecordV2(name="a", value="1"),
                               TextValueRecordV2(name="b", value="2")),
    "keyed_text_relation": {"k": "v"},
    "keyed_set_relation": {"k": ["a", "b"]},
    "keyed_record_relation": {"k": TextValueRecordV2(name="n", value="v")},
}


def test_the_oracle_and_the_runtime_catalog_declare_the_same_normalizers() -> None:
    """P1B's reconciliation. A normaliser added to the runtime without a
    coverage row here is RED, which is what the predecessor had no mechanism for."""
    assert set(_EXPECTED_NORMALIZERS) == set(normalizer_ids()), (
        f"oracle-only: {sorted(set(_EXPECTED_NORMALIZERS) - normalizer_ids())}; "
        f"runtime-only (NO COVERAGE ROW): {sorted(normalizer_ids() - set(_EXPECTED_NORMALIZERS))}"
    )


@pytest.mark.parametrize("normalizer_id", sorted(_EXPECTED_NORMALIZERS))
def test_every_normalizer_declares_the_policy_the_oracle_expects(normalizer_id: str) -> None:
    spec = normalizer_spec(normalizer_id)
    expected = _EXPECTED_NORMALIZERS[normalizer_id]
    assert spec.input_shape is expected["input_shape"]
    assert spec.output_semantic_shape is expected["output"]
    assert spec.exact_type_policy is expected["exact"]
    assert spec.duplicate_policy is expected["duplicates"]
    assert spec.ordering_policy is expected["ordering"]
    assert spec.encoding_policy is expected["encoding"]


@pytest.mark.parametrize("normalizer_id", sorted(_EXPECTED_NORMALIZERS))
def test_every_normalizer_accepts_its_own_valid_input(normalizer_id: str) -> None:
    """The control. A layer that refuses everything is trivially safe and useless."""
    assert normalize(normalizer_id, _VALID_INPUT[normalizer_id], "f") is not None


@pytest.mark.parametrize("normalizer_id", sorted(_EXPECTED_NORMALIZERS))
def test_every_normalizer_has_a_valid_input_declared(normalizer_id: str) -> None:
    assert normalizer_id in _VALID_INPUT, "a normaliser with no control is not covered"


# --------------------------------------------------------------------------
# P1A -- exact type closure, per declared policy
# --------------------------------------------------------------------------


class _TextSubclass(str):
    """Caller-owned, and it owns __eq__/__ne__/__hash__/__lt__ -- the operations
    duplicate rejection, ordering and identity comparison are made of."""


class _IntSubclass(int):
    pass


_EXACT_TYPE_ATTACKS = {
    ExactTypePolicyV2.EXACT_STR: [
        ("str subclass", _TextSubclass("text")),
        ("lone surrogate", "\ud800"),
        ("int where text declared", 1),
        ("bytes where text declared", b"text"),
    ],
    ExactTypePolicyV2.EXACT_INT: [
        ("int subclass", _IntSubclass(7)),
        ("bool as int", True),
        ("float as int", 7.0),
        ("str as int", "7"),
    ],
    ExactTypePolicyV2.EXACT_BOOL: [
        ("int as bool", 1),
        ("str as bool", "True"),
    ],
    ExactTypePolicyV2.EXACT_REGISTERED_RECORD: [
        ("unregistered type", object()),
        ("plain str", "not-a-record"),
    ],
}


@pytest.mark.parametrize("normalizer_id", sorted(_EXPECTED_NORMALIZERS))
def test_the_scalar_exact_type_policy_is_enforced(normalizer_id: str) -> None:
    """Driven by the DECLARED policy, so a new normaliser inherits the matrix."""
    spec = normalizer_spec(normalizer_id)
    if spec.input_shape is not InputShapeV2.SCALAR:
        return
    for label, hostile in _EXACT_TYPE_ATTACKS[spec.exact_type_policy]:
        with pytest.raises(SemanticIngressError):
            normalize(normalizer_id, hostile, f"f[{label}]")


def test_a_record_subclass_is_refused_where_the_registered_type_is_declared() -> None:
    class Widened(TextValueRecordV2):
        pass

    try:
        with pytest.raises(SemanticIngressError, match="registered"):
            normalize("exact_record", Widened(name="n", value="v"), "f")
    finally:
        # `__subclasses__()` is a global graph, so a leaked test class would make
        # the universe reconciliation fail for every later test.
        del Widened
        gc.collect()


# --------------------------------------------------------------------------
# P1A -- the record universe is reconciled, not hand-maintained
# --------------------------------------------------------------------------


def test_the_record_universe_is_reconciled_against_reachable_subclasses() -> None:
    gc.collect()
    ingress.assert_record_universe_is_reconciled()
    assert {r.__qualname__ for r in concrete_record_types()} == {
        "TextValueRecordV2", "BoundedIntRecordV2",
    }


def test_an_unregistered_concrete_record_makes_the_reconciliation_red() -> None:
    """A new record class is RED until classified. The predecessor kept a
    hand-maintained tuple that no mechanism ever checked."""

    class Unregistered(SemanticRecordV2):
        __slots__ = ()

    try:
        with pytest.raises(SemanticIngressError, match="unregistered"):
            ingress.assert_record_universe_is_reconciled()
    finally:
        del Unregistered
        gc.collect()
    ingress.assert_record_universe_is_reconciled()


# --------------------------------------------------------------------------
# P1B -- the ingress inventory
# --------------------------------------------------------------------------


def test_the_ingress_inventory_is_total() -> None:
    ingress._assert_ingress_inventory_is_total()


def test_a_free_standing_normalizer_makes_the_inventory_red() -> None:
    """The #336 root cause, as a mechanism rather than a memory.

    `_as_keyed_relation` was a normaliser nothing required to be catalogued.
    Here an uncatalogued module-level callable fails the inventory.
    """
    def _normalize_something_uncatalogued(value, field, spec):  # pragma: no cover
        return value

    _normalize_something_uncatalogued.__module__ = ingress.__name__
    ingress.__dict__["_normalize_something_uncatalogued"] = _normalize_something_uncatalogued
    try:
        with pytest.raises(SemanticIngressError, match="unclassified"):
            ingress._assert_ingress_inventory_is_total()
    finally:
        del ingress.__dict__["_normalize_something_uncatalogued"]
    ingress._assert_ingress_inventory_is_total()


def test_a_catalogued_normalizer_that_is_not_defined_here_is_refused() -> None:
    original = dict(ingress._NORMALIZER_CATALOG)
    try:
        borrowed = dataclasses.replace(
            original["exact_text"], normalizer_id="borrowed", implementation=len
        )
        ingress._NORMALIZER_CATALOG["borrowed"] = borrowed
        with pytest.raises(SemanticIngressError, match="not defined here"):
            ingress._assert_ingress_inventory_is_total()
    finally:
        ingress._NORMALIZER_CATALOG.clear()
        ingress._NORMALIZER_CATALOG.update(original)
    ingress._assert_ingress_inventory_is_total()


# --------------------------------------------------------------------------
# the keyed relation property -- #336's witnesses as general consequences
# --------------------------------------------------------------------------

_KEYED = ["keyed_text_relation", "keyed_set_relation", "keyed_record_relation"]


def _keyed_value(normalizer_id: str):
    return {"keyed_text_relation": "v",
            "keyed_set_relation": ["a"],
            "keyed_record_relation": TextValueRecordV2(name="n", value="v")}[normalizer_id]


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_a_set_cannot_become_an_ordered_key_value_pair(normalizer_id: str) -> None:
    """Hash order would decide which member is the KEY, so one logical input
    would normalise differently in every process."""
    with pytest.raises(SemanticIngressError, match="two-element sequence"):
        normalize(normalizer_id, [{"class_a", "class_b"}], "f")


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_a_mapping_used_as_a_pair_cannot_discard_its_values(normalizer_id: str) -> None:
    with pytest.raises(SemanticIngressError, match="two-element sequence"):
        normalize(normalizer_id, [{"class_a": "A", "class_b": "B"}], "f")


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_a_two_character_string_is_not_a_pair(normalizer_id: str) -> None:
    with pytest.raises(SemanticIngressError, match="two-element sequence"):
        normalize(normalizer_id, ["ab"], "f")


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_a_key_passes_the_exact_text_sealer(normalizer_id: str) -> None:
    value = _keyed_value(normalizer_id)
    for label, hostile_key in (("str subclass", _TextSubclass("k")),
                               ("lone surrogate", "\ud800"),
                               ("non-text", 1)):
        with pytest.raises(SemanticIngressError, match="key"):
            normalize(normalizer_id, [(hostile_key, value)], f"f[{label}]")


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_duplicate_keys_are_refused(normalizer_id: str) -> None:
    value = _keyed_value(normalizer_id)
    with pytest.raises(SemanticIngressError, match="duplicate keys"):
        normalize(normalizer_id, [("k", value), ("k", value)], "f")


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_key_ordering_is_canonical(normalizer_id: str) -> None:
    value = _keyed_value(normalizer_id)
    forward = normalize(normalizer_id, [("a", value), ("b", value)], "f")
    backward = normalize(normalizer_id, [("b", value), ("a", value)], "f")
    assert forward == backward
    assert [key for key, _ in forward] == ["a", "b"]


@pytest.mark.parametrize("normalizer_id", _KEYED)
def test_the_value_passes_its_declared_member_normalizer(normalizer_id: str) -> None:
    spec = normalizer_spec(normalizer_id)
    hostile = {"exact_text": _TextSubclass("v"),
               "string_set": "a-bare-string",
               "exact_record": "not-a-record"}[spec.member_normalizer_id]
    with pytest.raises(SemanticIngressError):
        normalize(normalizer_id, [("k", hostile)], "f")


# --------------------------------------------------------------------------
# collection semantics must EXECUTE
# --------------------------------------------------------------------------


def test_declared_semantics_select_the_normalizer() -> None:
    assert select_normalizer(CollectionSemanticsV2.SET, MemberKindV2.TEXT).normalizer_id == (
        "string_set"
    )
    assert select_normalizer(
        CollectionSemanticsV2.ORDERED, MemberKindV2.RECORD
    ).normalizer_id == "ordered_unique_records"
    assert select_normalizer(CollectionSemanticsV2.KEYED, MemberKindV2.TEXT).normalizer_id == (
        "keyed_text_relation"
    )


def test_an_undeclared_semantics_pairing_is_refused() -> None:
    with pytest.raises(SemanticIngressError, match="no normaliser for"):
        select_normalizer(CollectionSemanticsV2.ORDERED, MemberKindV2.TEXT)


def test_set_semantics_absorb_duplicates_and_ignore_caller_order() -> None:
    spec = select_normalizer(CollectionSemanticsV2.SET, MemberKindV2.TEXT)
    forward = normalize(spec.normalizer_id, ["a", "b"], "f")
    backward = normalize(spec.normalizer_id, ["b", "a"], "f")
    assert forward == backward
    assert normalize(spec.normalizer_id, ["a", "a", "b"], "f") == forward


def test_ordered_semantics_make_caller_order_meaning_bearing() -> None:
    spec = select_normalizer(CollectionSemanticsV2.ORDERED, MemberKindV2.RECORD)
    one = TextValueRecordV2(name="a", value="1")
    two = TextValueRecordV2(name="b", value="2")
    assert normalize(spec.normalizer_id, (one, two), "f") != (
        normalize(spec.normalizer_id, (two, one), "f")
    )
    with pytest.raises(SemanticIngressError, match="duplicate members"):
        normalize(spec.normalizer_id, (one, one), "f")
    with pytest.raises(SemanticIngressError, match="ordered input"):
        normalize(spec.normalizer_id, {one, two}, "f")


def test_the_dispatch_table_covers_exactly_the_declared_pairings() -> None:
    """The table is executable, so its entries must all resolve."""
    for (semantics, member_kind) in ingress._SEMANTICS_DISPATCH:
        assert select_normalizer(semantics, member_kind).normalizer_id in normalizer_ids()


# --------------------------------------------------------------------------
# interpreter registry -- a dispatch map, tested at cardinality > 1
# --------------------------------------------------------------------------


def test_the_registry_dispatches_distinguishable_interpreters() -> None:
    registry = InterpreterRegistryV2({
        "synthetic.alpha": lambda value: ("alpha", value),
        "synthetic.beta": lambda value: ("beta", value),
    })
    assert registry.algorithm_ids() == {"synthetic.alpha", "synthetic.beta"}
    assert registry.dispatch("synthetic.alpha")(1) == ("alpha", 1)
    assert registry.dispatch("synthetic.beta")(1) == ("beta", 1)
    assert registry.dispatch("synthetic.alpha") is not registry.dispatch("synthetic.beta")


def test_an_unknown_algorithm_id_is_refused() -> None:
    registry = InterpreterRegistryV2({"synthetic.alpha": lambda value: value})
    with pytest.raises(SemanticIngressError, match="names no interpreter"):
        registry.dispatch("synthetic.gamma")


def test_the_registry_refuses_a_str_subclass_as_an_algorithm_id() -> None:
    registry = InterpreterRegistryV2({"synthetic.alpha": lambda value: value})
    with pytest.raises(SemanticIngressError, match="exactly str"):
        registry.dispatch(_TextSubclass("synthetic.alpha"))


# --------------------------------------------------------------------------
# text policy, stated explicitly
# --------------------------------------------------------------------------


def test_text_requires_utf8_encodability() -> None:
    with pytest.raises(SemanticIngressError, match="UTF-8 encodable"):
        normalize("exact_text", "\ud800", "f")
    assert normalize("exact_text", "árvore \U0001f600", "f") == "árvore \U0001f600"


def test_unicode_normal_forms_are_distinct_values_by_stated_policy() -> None:
    """Declared, not accidental: folding NFC and NFD would be a canonicalisation
    this layer has no authority to choose."""
    import unicodedata

    nfc = unicodedata.normalize("NFC", "é")
    nfd = unicodedata.normalize("NFD", "é")
    assert nfc != nfd
    assert normalize("exact_text", nfc, "f") != normalize("exact_text", nfd, "f")
    with pytest.raises(SemanticIngressError, match="duplicate keys"):
        normalize("keyed_text_relation", [(nfc, "a"), (nfc, "b")], "f")


def test_records_seal_their_own_fields_through_the_catalog() -> None:
    with pytest.raises(SemanticIngressError, match="expected exactly str"):
        TextValueRecordV2(name=_TextSubclass("n"), value="v")
    with pytest.raises(SemanticIngressError, match="expected exactly int"):
        BoundedIntRecordV2(name="n", minimum=True, inclusive=True)
    with pytest.raises(SemanticIngressError, match="expected exactly bool"):
        BoundedIntRecordV2(name="n", minimum=1, inclusive=1)


def test_a_set_field_refuses_inputs_that_iterate_into_something_else() -> None:
    for hostile in ("a-bare-string", {"k": "v"}):
        with pytest.raises(SemanticIngressError, match="set or sequence"):
            normalize("string_set", hostile, "f")


def test_normalize_refuses_an_undeclared_normalizer() -> None:
    with pytest.raises(SemanticIngressError, match="no normaliser declared"):
        normalize(None, "x", "f")
    with pytest.raises(SemanticIngressError, match="no catalogued normaliser"):
        normalize("not_a_normalizer", "x", "f")
