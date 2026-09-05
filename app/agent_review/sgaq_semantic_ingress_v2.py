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
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from typing import ClassVar

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


_POLICY_TYPES: dict[str, type] = {"exact_str": str, "exact_int": int, "exact_bool": bool}


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

    Three things here are structural rather than conventional, each because
    review reached the domain through the gap it leaves.

    `FIELD_SEMANTICS` declares, per field, the `(collection semantics, member
    kind)` pairing that governs it, and the base seals every field FROM that
    declaration. Sealing is therefore not something a record author writes and
    can forget: a record whose fields are not all declared is refused at
    registration, and there is no hand-written `__post_init__` to omit. Review
    registered a record with an unsealed `payload: object` field and walked a
    `str` subclass and a lone surrogate straight into the universe.

    Registration is keyed by class IDENTITY, never by `__qualname__`. A qualname
    omits the module, so two slices each defining `AuditRecordV2` silently
    evicted one another, and a class that merely *claimed* a registered qualname
    displaced the real type from the enumerated universe while reconciliation
    reported green.

    `slots=True` is deliberately not used. It installs a public `__setstate__`
    that rewrites frozen fields in place with `object.__setattr__` -- an
    unsealing mutator needing no pickle and no bytes -- and it duplicates every
    class in `__subclasses__()`, which is what forced the name-keyed dedupe the
    collisions above exploited.
    """

    #: field name -> (collection semantics, member kind). Declared, and executed.
    FIELD_SEMANTICS: ClassVar[Mapping[str, tuple["CollectionSemanticsV2", "MemberKindV2"]]] = {}

    def __post_init__(self) -> None:
        for name, (semantics, member_kind) in type(self).FIELD_SEMANTICS.items():
            spec = select_normalizer(semantics, member_kind)
            object.__setattr__(
                self, name, normalize(spec.normalizer_id, getattr(self, name), name)
            )


_REGISTERED_RECORD_TYPES: dict[type, None] = {}
_UNIVERSE_SEALED = False


def semantic_record(cls: type) -> type:
    """Admit a TYPE into the closed universe, validating what that requires.

    This is the widest ingress in the module -- it decides which types the
    domain may hold -- and it previously validated nothing at all, so a class
    that was not even a `SemanticRecordV2` could be registered and admitted.
    """
    if _UNIVERSE_SEALED:
        raise SemanticIngressError(
            f"{cls!r}: the record universe is sealed; a type admitted after "
            f"reconciliation would never be checked against it"
        )
    if not isinstance(cls, type) or not issubclass(cls, SemanticRecordV2):
        raise SemanticIngressError(f"{cls!r} is not a SemanticRecordV2 subclass")
    if not dataclasses.is_dataclass(cls) or not cls.__dataclass_params__.frozen:
        raise SemanticIngressError(f"{cls!r} must be a frozen dataclass")
    declared = set(cls.FIELD_SEMANTICS)
    actual = {f.name for f in dataclasses.fields(cls)}
    if declared != actual:
        raise SemanticIngressError(
            f"{cls.__qualname__}: field semantics not total: "
            f"undeclared={sorted(actual - declared)} stale={sorted(declared - actual)}"
        )
    for name, (semantics, member_kind) in cls.FIELD_SEMANTICS.items():
        select_normalizer(semantics, member_kind)  # refuses an undeclared pairing
    _REGISTERED_RECORD_TYPES[cls] = None
    return cls


def concrete_record_types() -> tuple[type, ...]:
    """Every concrete subclass reachable from the abstract base, by identity.

    `type.__subclasses__` is called unbound so a metaclass cannot intercept it;
    review hid an entire subtree by overriding it.
    """
    found: list[type] = []

    def walk(base: type) -> None:
        for child in type.__subclasses__(base):
            found.append(child)
            walk(child)

    walk(SemanticRecordV2)
    return tuple(found)


def assert_record_universe_is_reconciled() -> None:
    """Declared registry == concrete types actually reachable, by identity."""
    declared = set(_REGISTERED_RECORD_TYPES)
    actual = set(concrete_record_types())
    if declared != actual:
        raise SemanticIngressError(
            f"semantic record universe is not reconciled: "
            f"unregistered={sorted(c.__qualname__ for c in actual - declared)} "
            f"stale={sorted(c.__qualname__ for c in declared - actual)}"
        )


# --------------------------------------------------------------------------
# normaliser implementations -- every one of them catalogued below
# --------------------------------------------------------------------------


_EXACT_TYPE_OF_POLICY: dict[ExactTypePolicyV2, type] = {
    policy: _POLICY_TYPES[policy.value]
    for policy in ExactTypePolicyV2
    if policy.value in _POLICY_TYPES
}


def _normalize_exact_scalar(value: object, field: str, spec: NormalizerSpecV2) -> object:
    """One implementation, driven by the DECLARED policy columns.

    `exact_type_policy` and `encoding_policy` are read here rather than merely
    recorded. Review found six of nine columns never read at runtime, so a
    declaration could contradict behaviour and only a hand-copied oracle row
    would have to change -- the decorative-metadata failure the predecessor was
    stopped for, reproduced in the policy table while being fixed in dispatch.
    """
    expected = _EXACT_TYPE_OF_POLICY[spec.exact_type_policy]
    if type(value) is not expected:
        raise SemanticIngressError(
            f"{field}: expected exactly {expected.__name__}, got {type(value).__name__}"
        )
    if spec.encoding_policy is EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED:
        try:
            value.encode("utf-8")  # type: ignore[union-attr]
        except UnicodeEncodeError as exc:
            raise SemanticIngressError(
                f"{field}: is not UTF-8 encodable, so it can carry no identity"
            ) from exc
    return value


def _normalize_registered_record(
    value: object, field: str, spec: NormalizerSpecV2
) -> object:
    """Exactly a registered record type, RE-SEALED at the boundary.

    Type identity alone was not enough. Sealing used to be a construction-time
    property that frozen-ness was assumed to preserve, and frozen-ness has
    escape hatches -- review rewrote a sealed record's fields in place and the
    unsealed record was admitted, because nothing re-checked its contents. So
    every field is re-normalised here and required to be unchanged. That is
    depth-agnostic: it holds however the mutation was performed.
    """
    if type(value) not in _REGISTERED_RECORD_TYPES:
        raise SemanticIngressError(
            f"{field}: expected exactly a registered semantic record, "
            f"got {type(value).__name__}"
        )
    for name, (semantics, member_kind) in type(value).FIELD_SEMANTICS.items():
        member_spec = select_normalizer(semantics, member_kind)
        current = getattr(value, name)
        resealed = normalize(member_spec.normalizer_id, current, f"{field}.{name}")
        if resealed != current or type(resealed) is not type(current):
            raise SemanticIngressError(
                f"{field}.{name}: does not re-seal to itself; the record was "
                f"altered after construction"
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
    if not isinstance(value, (Set, Sequence)):
        raise SemanticIngressError(
            f"{field}: expected a set or sequence of members, got {type(value).__name__}"
        )
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
    try:
        distinct = len(set(members))
    except TypeError as exc:
        raise SemanticIngressError(f"{field}: members must be hashable") from exc
    if distinct != len(members):
        raise SemanticIngressError(f"{field}: duplicate members are refused")
    return members


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
        # The mapping branch used to trust `.items()` and unpack it; a
        # caller-owned Mapping yielding a 3-tuple escaped as a raw ValueError,
        # so a caller catching SemanticIngressError did not fail closed.
        pairs = []
        for item in value.items():
            if not isinstance(item, Sequence) or len(item) != 2:
                raise SemanticIngressError(
                    f"{field}: a pair must be an ordered two-element sequence"
                )
            pairs.append((item[0], item[1]))
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

    keys = [normalize(spec.key_normalizer_id, key, f"{field} key") for key, _ in pairs]
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
    #: Keyed relations seal their KEYS through a declared normaliser too. It was
    #: hardcoded, so the keyed rows' exact-type and encoding columns described
    #: only their members and contradicted observed behaviour.
    key_normalizer_id: str | None = None


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
              implementation=_normalize_exact_scalar),
        _spec(normalizer_id="exact_int", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_INT,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_exact_scalar),
        _spec(normalizer_id="exact_bool", input_shape=InputShapeV2.SCALAR,
              output_semantic_shape=OutputShapeV2.SCALAR,
              exact_type_policy=ExactTypePolicyV2.EXACT_BOOL,
              duplicate_policy=DuplicatePolicyV2.NOT_APPLICABLE,
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
              encoding_policy=EncodingPolicyV2.NOT_APPLICABLE,
              implementation=_normalize_exact_scalar),
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
              # A frozenset has no order. Declaring CANONICAL_SORT here was
              # false the moment it was written, and the oracle could not see it
              # because it compared the declaration to a copy of itself.
              ordering_policy=OrderingPolicyV2.NOT_APPLICABLE,
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
              implementation=_normalize_keyed_relation, member_normalizer_id="exact_text",
              key_normalizer_id="exact_text"),
        _spec(normalizer_id="keyed_set_relation", input_shape=InputShapeV2.MAPPING_OR_PAIRS,
              output_semantic_shape=OutputShapeV2.KEYED_PAIRS,
              exact_type_policy=ExactTypePolicyV2.EXACT_STR,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_keyed_relation, member_normalizer_id="string_set",
              key_normalizer_id="exact_text"),
        _spec(normalizer_id="keyed_record_relation", input_shape=InputShapeV2.MAPPING_OR_PAIRS,
              output_semantic_shape=OutputShapeV2.KEYED_PAIRS,
              exact_type_policy=ExactTypePolicyV2.EXACT_REGISTERED_RECORD,
              duplicate_policy=DuplicatePolicyV2.REJECT,
              ordering_policy=OrderingPolicyV2.CANONICAL_SORT,
              # Keys of every keyed relation are text and must be encodable;
              # declaring NOT_APPLICABLE here contradicted observed behaviour.
              encoding_policy=EncodingPolicyV2.UTF8_ENCODABLE_REQUIRED,
              implementation=_normalize_keyed_relation, member_normalizer_id="exact_record",
              key_normalizer_id="exact_text"),
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
    """The single channel. Every admission goes through a catalogued normaliser.

    The identifier itself is exact-typed. A `str` subclass owning `__hash__` and
    `__eq__` could otherwise select a different channel than the one it spells,
    which is the same defect as letting one own an identity comparison -- at the
    boundary that chooses which sealing policy runs.
    """
    if normalizer_id is None:
        raise SemanticIngressError(f"{field}: no normaliser declared")
    if type(normalizer_id) is not str:
        raise SemanticIngressError(
            f"{field}: normaliser id must be exactly str, got {type(normalizer_id).__name__}"
        )
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
        sealed: dict[str, Callable[..., object]] = {}
        for key, interpreter in dict(interpreters).items():
            # The STORAGE side needs the same discipline as the lookup side. A
            # `str` subclass stored as a key owns the comparison `dispatch`
            # performs, so review made a value declaring v2 be interpreted by
            # v1 -- through the constructor, the hazard this class names.
            sealed_key = normalize("exact_text", key, "algorithm id")
            if not callable(interpreter):
                raise SemanticIngressError(
                    f"{sealed_key!r}: interpreter must be callable, "
                    f"got {type(interpreter).__name__}"
                )
            sealed[sealed_key] = interpreter  # type: ignore[index]
        self._interpreters = sealed

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
@dataclasses.dataclass(frozen=True)
class TextValueRecordV2(SemanticRecordV2):
    """A named text value. Sealing is generated from the declaration below."""

    FIELD_SEMANTICS: ClassVar[Mapping[str, tuple[CollectionSemanticsV2, MemberKindV2]]] = {
        "name": (CollectionSemanticsV2.SCALAR, MemberKindV2.TEXT),
        "value": (CollectionSemanticsV2.SCALAR, MemberKindV2.TEXT),
    }

    name: str
    value: str


@semantic_record
@dataclasses.dataclass(frozen=True)
class BoundedIntRecordV2(SemanticRecordV2):
    """A named integer bound, exercising the int and bool normalisers."""

    FIELD_SEMANTICS: ClassVar[Mapping[str, tuple[CollectionSemanticsV2, MemberKindV2]]] = {
        "name": (CollectionSemanticsV2.SCALAR, MemberKindV2.TEXT),
        "minimum": (CollectionSemanticsV2.SCALAR, MemberKindV2.INTEGER),
        "inclusive": (CollectionSemanticsV2.SCALAR, MemberKindV2.BOOLEAN),
    }

    name: str
    minimum: int
    inclusive: bool


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
