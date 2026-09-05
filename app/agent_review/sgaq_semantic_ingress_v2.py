"""Every way a value can enter SGAQ semantic state, inventoried.

`#331`, SGAQ-S0B-A0. Two propositions, and nothing else:

    P1A_SEMANTIC_TYPE_CLOSURE -- every runtime value admitted into future SGAQ
    semantic state belongs to an explicitly closed exact-type universe.

    P1B_SEMANTIC_INGRESS_CLOSURE -- every function capable of admitting or
    normalising a value into that universe is mechanically inventoried and
    applies the declared sealing discipline.

There is no contract here, no semantic identity, no digest, no carrier, no
observation, no plan. Those come later and are deliberately absent: the
predecessor built them on top of an ingress layer nobody had inventoried.

## What went wrong three times, and what is different

PR #335 sealed one field of a multi-field domain; review performed the same
prohibited move through two neighbouring fields. PR #336 sealed the outer
dataclass; review performed it one level down, in the nested records. #336 was
then corrected to seal the record closure -- and review performed it a third
time, through `_as_keyed_relation`, the one normaliser never brought under the
discipline, and through four `isinstance` gates that let any `str` subclass in.

Each correction was scoped to the arms a reviewer happened to show. The missing
mechanism was never another guard: it was the obligation to enumerate *every
function that admits a value*, so a normaliser outside the discipline is a
failing check rather than a thing someone has to notice.

That is what `_NORMALIZER_CATALOG` and `_assert_ingress_inventory_is_total` are.
Every module-level callable is either the implementation of a catalogued
normaliser or explicitly declared non-ingress, reconciled at import. A new
free-standing normaliser cannot be added quietly; the module refuses to load.

## Exact types, because a subclass owns the comparisons

`isinstance` is not used anywhere a subclass could change semantic behaviour.
This is not fastidiousness. A `str` subclass is caller-owned and defines
`__eq__`, `__ne__`, `__hash__` and `__lt__` -- the very operations duplicate
rejection, canonical ordering and identity comparison are made of. Measured on
the predecessor: a subclass with a state-dependent `__eq__` passed the
duplicate-key guard at construction and then collapsed two entries into one
during projection, so two contracts with different content shared an identity;
another whose `__ne__` returned `False` took reflected priority over a rederived
digest and made a carrier authorise itself.

`bool` is refused where `int` is declared, for the same reason in miniature:
`True` and `1` are one JSON value under two intents.

## Declared policy, and policy that executes

Every normaliser declares its input shape, output shape, exact-type policy,
duplicate policy, ordering policy and encoding policy. Those declarations are
not documentation -- `select_normalizer` maps collection semantics onto them, so
changing a declaration changes which normaliser runs. The predecessor carried a
`_COLLECTION_SEMANTICS` table whose values were never reconciled against the
implementation, and declaring a set field "ordered" left its suite green.

## Text policy, stated rather than assumed

Text is exactly `str` and must be UTF-8 encodable. A lone surrogate is a
perfectly constructible Python string that no UTF-8 canonicalisation can
serialise, so admitting one would produce a value that can never carry an
identity. No Unicode normal form is imposed: NFC and NFD spellings are distinct
values here, deliberately, because silently folding them would be a
canonicalisation this layer has no authority to choose. That is a stated policy,
not an oversight, and a consumer that needs folding must declare it.

## What this layer does not claim

Pickle is not an acquisition boundary. `dataclasses` may happen to support it;
that is an implementation property, not an authorised path for untrusted bytes,
and no guard here pretends otherwise. The authorised external path, when a later
slice needs one, is strict bytes -> strict parser -> validated constructor.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Callable, Iterable, Mapping, Sequence

__all__ = [
    "BoundedIntRecordV2",
    "CollectionSemanticsV2",
    "DuplicatePolicyV2",
    "EncodingPolicyV2",
    "ExactTypePolicyV2",
    "InputShapeV2",
    "InterpreterRegistryV2",
    "MemberKindV2",
    "NormalizerSpecV2",
    "OrderingPolicyV2",
    "OutputShapeV2",
    "SemanticIngressError",
    "SemanticRecordV2",
    "TextValueRecordV2",
    "concrete_record_types",
    "normalize",
    "normalizer_ids",
    "normalizer_spec",
    "select_normalizer",
]


class SemanticIngressError(AssertionError):
    """A value was offered to the semantic domain that the domain cannot hold."""


# --------------------------------------------------------------------------
# declared policy vocabularies
# --------------------------------------------------------------------------


class InputShapeV2(enum.Enum):
    SCALAR = "scalar"
    ITERABLE_OF_SCALAR = "iterable_of_scalar"
    ITERABLE_OF_RECORD = "iterable_of_record"
    MAPPING_OR_PAIRS = "mapping_or_pairs"


class OutputShapeV2(enum.Enum):
    SCALAR = "scalar"
    FROZEN_SET = "frozen_set"
    ORDERED_TUPLE = "ordered_tuple"
    KEYED_PAIRS = "keyed_pairs"


class ExactTypePolicyV2(enum.Enum):
    EXACT_STR = "exact_str"
    EXACT_INT = "exact_int"
    EXACT_BOOL = "exact_bool"
    EXACT_REGISTERED_RECORD = "exact_registered_record"


class DuplicatePolicyV2(enum.Enum):
    #: A set absorbs a repeated member: that is what a set means.
    ABSORB = "absorb"
    #: Anywhere a repeat would be a contradiction rather than a restatement.
    REJECT = "reject"
    NOT_APPLICABLE = "not_applicable"


class OrderingPolicyV2(enum.Enum):
    CANONICAL_SORT = "canonical_sort"
    CALLER_ORDER_IS_SEMANTIC = "caller_order_is_semantic"
    NOT_APPLICABLE = "not_applicable"


class EncodingPolicyV2(enum.Enum):
    UTF8_ENCODABLE_REQUIRED = "utf8_encodable_required"
    NOT_APPLICABLE = "not_applicable"


class CollectionSemanticsV2(enum.Enum):
    """What a field's collection MEANS. Selects the normaliser; never decorative."""

    SCALAR = "scalar"
    SET = "set"
    ORDERED = "ordered"
    KEYED = "keyed"


class MemberKindV2(enum.Enum):
    TEXT = "text"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    TEXT_SET = "text_set"
    RECORD = "record"


# --------------------------------------------------------------------------
# the semantic record universe, mechanically reconciled
# --------------------------------------------------------------------------


class SemanticRecordV2:
    """Abstract base for every record type the semantic domain may hold.

    Concrete subclasses must be registered with `@semantic_record`. The registry
    is reconciled at import against the subclasses actually reachable, so a new
    record class is an import failure until it is classified -- the predecessor
    kept a hand-maintained tuple that no mechanism ever checked.
    """

    __slots__ = ()


_REGISTERED_RECORD_TYPES: dict[str, type] = {}


def semantic_record(cls: type) -> type:
    """Register a concrete record type into the closed universe."""
    _REGISTERED_RECORD_TYPES[cls.__qualname__] = cls
    return cls


def concrete_record_types() -> tuple[type, ...]:
    """Every concrete subclass reachable from the abstract base.

    Deduplicated by qualified name: a `slots=True` dataclass appears twice in
    `__subclasses__()`, once before and once after the class is rebuilt with
    slots, and counting it twice would make the reconciliation unreadable.
    """
    found: dict[str, type] = {}

    def walk(base: type) -> None:
        for child in base.__subclasses__():
            if child is not SemanticRecordV2:
                found[child.__qualname__] = child
            walk(child)

    walk(SemanticRecordV2)
    return tuple(found[name] for name in sorted(found))


def assert_record_universe_is_reconciled() -> None:
    """Declared registry == concrete types actually reachable."""
    declared = set(_REGISTERED_RECORD_TYPES)
    actual = {record.__qualname__ for record in concrete_record_types()}
    if declared != actual:
        raise SemanticIngressError(
            f"semantic record universe is not reconciled: "
            f"unregistered={sorted(actual - declared)} stale={sorted(declared - actual)}"
        )


# --------------------------------------------------------------------------
# normaliser implementations -- every one of them catalogued below
# --------------------------------------------------------------------------


def _normalize_exact_text(value: object, field: str, spec: NormalizerSpecV2) -> str:
    if type(value) is not str:
        raise SemanticIngressError(
            f"{field}: expected exactly str, got {type(value).__name__}"
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SemanticIngressError(
            f"{field}: is not UTF-8 encodable, so it can carry no identity"
        ) from exc
    return value


def _normalize_exact_int(value: object, field: str, spec: NormalizerSpecV2) -> int:
    if type(value) is not int:
        raise SemanticIngressError(
            f"{field}: expected exactly int, got {type(value).__name__}"
        )
    return value


def _normalize_exact_bool(value: object, field: str, spec: NormalizerSpecV2) -> bool:
    if type(value) is not bool:
        raise SemanticIngressError(
            f"{field}: expected exactly bool, got {type(value).__name__}"
        )
    return value


def _normalize_string_set(
    value: object, field: str, spec: NormalizerSpecV2
) -> frozenset[str]:
    """A mathematical set of text, from an input that is actually one.

    `str` and `Mapping` are refused rather than iterated. Both were silent in the
    predecessor: a bare string explodes into its characters, and a mapping yields
    only its keys, so two contracts written to differ shared one identity.
    """
    _refuse_pseudo_iterable(value, field)
    return frozenset(
        normalize(spec.member_normalizer_id, member, f"{field} member")
        for member in value  # type: ignore[union-attr]
    )


def _normalize_ordered_unique_records(
    value: object, field: str, spec: NormalizerSpecV2
) -> tuple[object, ...]:
    """An ordered sequence of records whose order is semantic and members unique.

    The input must itself be ordered. A `set` handed to an ordered field freezes
    hash-table order into the value, so one logical input produces a different
    result in every process -- measured on the predecessor, where it made
    identity hash-seed dependent.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise SemanticIngressError(f"{field}: an ordered sequence requires an ordered input")
    members = tuple(
        normalize(spec.member_normalizer_id, member, f"{field} member") for member in value
    )
    if len(set(members)) != len(members):
        raise SemanticIngressError(f"{field}: duplicate members are refused")
    return members


def _normalize_registered_record(
    value: object, field: str, spec: NormalizerSpecV2
) -> object:
    """Exactly a registered concrete record type. Not `isinstance`.

    A subclass would be free to override whatever a later slice projects with,
    which is virtual dispatch at an authority boundary.
    """
    if type(value).__qualname__ not in _REGISTERED_RECORD_TYPES:
        raise SemanticIngressError(
            f"{field}: expected exactly a registered semantic record, "
            f"got {type(value).__name__}"
        )
    if type(value) is not _REGISTERED_RECORD_TYPES[type(value).__qualname__]:
        raise SemanticIngressError(f"{field}: record type is not the registered one")
    return value


def _normalize_keyed_relation(
    value: object, field: str, spec: NormalizerSpecV2
) -> tuple[tuple[str, object], ...]:
    """A keyed relation: canonical key order, duplicate keys refused.

    Every property the predecessor's `_as_keyed_relation` lacked is a
    consequence of this one implementation rather than a patch bolted onto it.
    Keys go through the text normaliser like any other text, so a `str`
    subclass, a lone surrogate and a non-string are refused here for the same
    reason they are refused anywhere. A pair must be an ordered two-element
    sequence, so a `set` cannot decide which member is the key by hash order and
    a `Mapping` cannot be flattened to its keys with its values discarded.
    """
    if isinstance(value, Mapping):
        pairs: list[tuple[object, object]] = list(value.items())
    elif not isinstance(value, (str, bytes)) and isinstance(value, Sequence):
        pairs = []
        for item in value:
            if isinstance(item, (str, bytes)) or not isinstance(item, Sequence):
                raise SemanticIngressError(
                    f"{field}: a pair must be an ordered two-element sequence"
                )
            if len(item) != 2:
                raise SemanticIngressError(
                    f"{field}: a pair must be an ordered two-element sequence"
                )
            pairs.append((item[0], item[1]))
    else:
        raise SemanticIngressError(f"{field}: expected a mapping or a sequence of pairs")

    keys = [normalize("exact_text", key, f"{field} key") for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise SemanticIngressError(f"{field}: duplicate keys are refused")
    normalized = [
        (key, normalize(spec.member_normalizer_id, raw, f"{field}[{key}]"))
        for key, (_, raw) in zip(keys, pairs)
    ]
    return tuple(sorted(normalized, key=lambda pair: pair[0]))


def _refuse_pseudo_iterable(value: object, field: str) -> None:
    """Inputs that iterate into something other than their members."""
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        raise SemanticIngressError(
            f"{field}: expected a set or sequence of members, got {type(value).__name__}"
        )


# --------------------------------------------------------------------------
# the catalog -- the single channel into semantic state
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class NormalizerSpecV2:
    """One catalogued way to admit a value, with its policy declared."""

    normalizer_id: str
    input_shape: InputShapeV2
    output_semantic_shape: OutputShapeV2
    exact_type_policy: ExactTypePolicyV2
    duplicate_policy: DuplicatePolicyV2
    ordering_policy: OrderingPolicyV2
    encoding_policy: EncodingPolicyV2
    implementation: Callable[[object, str, "NormalizerSpecV2"], object]
    member_normalizer_id: str | None = None


def _spec(**kwargs) -> NormalizerSpecV2:
    return NormalizerSpecV2(**kwargs)


_NORMALIZER_CATALOG: dict[str, NormalizerSpecV2] = {
    spec.normalizer_id: spec
    for spec in (
        _spec(normalizer_id="exact_text", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_STR,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_exact_text),
        _spec(normalizer_id="exact_int", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_INT,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_exact_int),
        _spec(normalizer_id="exact_bool", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_BOOL,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_exact_bool),
        _spec(normalizer_id="exact_record", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_registered_record),
        _spec(normalizer_id="string_set", input_shape=InputShapeV2.ITERABLE_OF_SCALAR,
              output_semantic_shape=OutputShapeV2.FROZEN_SET,
              exact_type_policy=ExactTypePolicyV2.EXACT_STR,
              duplicate_policy=DuplicatePolicyV2.ABSORB,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_string_set, member_normalizer_id="exact_text"),
        _spec(normalizer_id="ordered_unique_records",
              input_shape=InputShapeV2.ITERABLE_OF_RECORD,
              output_semantic_shape=OutputShapeV2.ORDERED_TUPLE,
              exact_type_policy=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CALLER_ORDER_IS_SEMANTIC,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_ordered_unique_records,
              member_normalizer_id="exact_record"),
        _spec(normalizer_id="keyed_text_relation", input_shape=InputShapeV2.MAPPING_OR_PAIRS,
              output_semantic_shape=OutputShapeV2.KEYED_PAIRS,
              exact_type_policy=ExactTypePolicyV2.EXACT_STR,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_keyed_relation, member_normalizer_id="exact_text"),
        _spec(normalizer_id="keyed_set_relation", input_shape=InputShapeV2.MAPPING_OR_PAIRS,
              output_semantic_shape=OutputShapeV2.KEYED_PAIRS,
              exact_type_policy=ExactTypePolicyV2.EXACT_STR,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_keyed_relation, member_normalizer_id="string_set"),
        _spec(normalizer_id="keyed_record_relation", input_shape=InputShapeV2.MAPPING_OR_PAIRS,
              output_semantic_shape=OutputShapeV2.KEYED_PAIRS,
              exact_type_policy=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_keyed_relation, member_normalizer_id="exact_record"),
    )
}


def normalizer_ids() -> frozenset[str]:
    return frozenset(_NORMALIZER_CATALOG)


def normalizer_spec(normalizer_id: str) -> NormalizerSpecV2:
    try:
        return _NORMALIZER_CATALOG[normalizer_id]
    except KeyError as exc:
        raise SemanticIngressError(f"no catalogued normaliser {normalizer_id!r}") from exc


def normalize(normalizer_id: str | None, value: object, field: str) -> object:
    """The single channel. Every admission goes through a catalogued normaliser."""
    if normalizer_id is None:
        raise SemanticIngressError(f"{field}: no normaliser declared")
    spec = normalizer_spec(normalizer_id)
    return spec.implementation(value, field, spec)


# --------------------------------------------------------------------------
# collection semantics SELECT the normaliser; the table is not decorative
# --------------------------------------------------------------------------

_SEMANTICS_DISPATCH: dict[tuple[CollectionSemanticsV2, MemberKindV2], str] = {
    (CollectionSemanticsV2.SCALAR, MemberKindV2.TEXT): "exact_text",
    (CollectionSemanticsV2.SCALAR, MemberKindV2.INTEGER): "exact_int",
    (CollectionSemanticsV2.SCALAR, MemberKindV2.BOOLEAN): "exact_bool",
    (CollectionSemanticsV2.SCALAR, MemberKindV2.RECORD): "exact_record",
    (CollectionSemanticsV2.SET, MemberKindV2.TEXT): "string_set",
    (CollectionSemanticsV2.ORDERED, MemberKindV2.RECORD): "ordered_unique_records",
    (CollectionSemanticsV2.KEYED, MemberKindV2.TEXT): "keyed_text_relation",
    (CollectionSemanticsV2.KEYED, MemberKindV2.TEXT_SET): "keyed_set_relation",
    (CollectionSemanticsV2.KEYED, MemberKindV2.RECORD): "keyed_record_relation",
}


def select_normalizer(
    semantics: CollectionSemanticsV2, member_kind: MemberKindV2
) -> NormalizerSpecV2:
    """Declared semantics decide which normaliser runs.

    The predecessor carried a semantics table its implementation never consulted,
    so declaring a set field "ordered" changed nothing and its suite stayed green.
    Here the declaration is the dispatch.
    """
    try:
        normalizer_id = _SEMANTICS_DISPATCH[(semantics, member_kind)]
    except KeyError as exc:
        raise SemanticIngressError(
            f"no normaliser for {semantics.value}/{member_kind.value}"
        ) from exc
    return normalizer_spec(normalizer_id)


# --------------------------------------------------------------------------
# interpreter registry -- a dispatch map, never a membership set
# --------------------------------------------------------------------------


class InterpreterRegistryV2:
    """Maps an algorithm identity to the exact callable that implements it.

    A membership set was measured to be wrong in the predecessor: it was correct
    only while it held one name, and adding a second silently reopened the very
    failure the identity field exists to prevent -- a value declaring `v2` being
    interpreted by `v1`.
    """

    def __init__(self, interpreters: Mapping[str, Callable[..., object]]) -> None:
        self._interpreters = dict(interpreters)

    def algorithm_ids(self) -> frozenset[str]:
        return frozenset(self._interpreters)

    def dispatch(self, algorithm_id: object) -> Callable[..., object]:
        if type(algorithm_id) is not str:
            raise SemanticIngressError(
                f"algorithm id must be exactly str, got {type(algorithm_id).__name__}"
            )
        try:
            return self._interpreters[algorithm_id]
        except KeyError as exc:
            raise SemanticIngressError(
                f"{algorithm_id!r} names no interpreter this registry implements"
            ) from exc


# --------------------------------------------------------------------------
# foundational record types
# --------------------------------------------------------------------------


@semantic_record
@dataclasses.dataclass(frozen=True, slots=True)
class TextValueRecordV2(SemanticRecordV2):
    """A named text value, sealed through the catalogued text normaliser."""

    name: str
    value: str

    def __post_init__(self) -> None:
        for field in ("name", "value"):
            object.__setattr__(self, field, normalize("exact_text", getattr(self, field), field))


@semantic_record
@dataclasses.dataclass(frozen=True, slots=True)
class BoundedIntRecordV2(SemanticRecordV2):
    """A named integer bound, exercising the int and bool normalisers."""

    name: str
    minimum: int
    inclusive: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", normalize("exact_text", self.name, "name"))
        object.__setattr__(self, "minimum", normalize("exact_int", self.minimum, "minimum"))
        object.__setattr__(
            self, "inclusive", normalize("exact_bool", self.inclusive, "inclusive")
        )


# --------------------------------------------------------------------------
# the ingress inventory -- P1B's mechanism
# --------------------------------------------------------------------------

#: Module-level callables that do NOT admit a value into the semantic domain.
#: Every other one must implement a catalogued normaliser. This is the check the
#: predecessor lacked: `_as_keyed_relation` was a free-standing normaliser that
#: no mechanism ever required to be under the discipline.
_NON_INGRESS_CALLABLES: frozenset[str] = frozenset({
    "semantic_record",
    "concrete_record_types",
    "assert_record_universe_is_reconciled",
    "normalizer_ids",
    "normalizer_spec",
    "normalize",
    "select_normalizer",
    "_spec",
    "_refuse_pseudo_iterable",
    "_assert_ingress_inventory_is_total",
})


def _assert_ingress_inventory_is_total() -> None:
    """Every module-level callable is catalogued or declared non-ingress."""
    catalogued = {spec.implementation.__name__ for spec in _NORMALIZER_CATALOG.values()}
    defined = {
        name
        for name, value in globals().items()
        if callable(value)
        and getattr(value, "__module__", None) == __name__
        and not isinstance(value, type)
    }
    unclassified = defined - catalogued - _NON_INGRESS_CALLABLES
    stale = _NON_INGRESS_CALLABLES - defined
    if unclassified or stale:
        raise SemanticIngressError(
            f"ingress inventory is not total: unclassified={sorted(unclassified)} "
            f"stale={sorted(stale)}"
        )
    orphaned = catalogued - defined
    if orphaned:
        raise SemanticIngressError(f"catalogued normaliser not defined here: {sorted(orphaned)}")


_assert_ingress_inventory_is_total()
assert_record_universe_is_reconciled()
