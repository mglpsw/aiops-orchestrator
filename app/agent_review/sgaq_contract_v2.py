"""The sealed SGAQ contract, and the identity derived from its semantics.

`#331`, SGAQ-S0B-A. This slice implements exactly one proposition and claims
exactly one closure:

    P1_DOMAIN_IMMUTABILITY -- every field capable of altering the SGAQ semantic
    contract is recursively immutable, belongs to one explicit semantic
    projection, and any semantic change changes the rederived semantic identity
    or is rejected.

It contains no Git-storage decision logic: no observation, no classification, no
admission, no plan, no materialisation. Those are later slices, and the point of
holding them out is that a contract which cannot be widened is worth having
before anything consumes it.

## Why sealing one field is not sealing

The predecessor (PR #335, stopped) sealed `required_classes` after review found
it mutable, and shipped a guard test scoped to that field. Review then performed
the same prohibited move -- meet an unadmittable requirement, delete it, rebuild,
receive a complete plan -- through `claims` and through `object_formats`, on an
unchanged contract object. The fix had been scoped to the witness rather than to
the proposition.

So the rule here is structural: *every* field is normalised in one place, before
any validation reads it, and the field universe is reconciled against an
independently written table in the tests. A new field is a failing test until it
is classified, which is the only mechanism that makes "we sealed the domain" a
statement about the domain rather than about the fields someone remembered.

## Three field classes, and why a name decides nothing

SEMANTIC iff changing it can change interpretation, admitted domain, decision
restriction, derivation, materialization semantics, or supported claim. ENVELOPE
iff it can change none of those. DERIVED_LABEL iff its only valid value is a
deterministic function of semantic identity.

`semantic_projection_algorithm_id` is the field that makes this rule earn its
keep. It is version-shaped, and it is unambiguously SEMANTIC: identical field
bytes read under a different projection mean something different, so the
interpreter is part of what the contract says. Classifying by name would have
filed it as envelope and left two contracts with different meanings sharing one
identity.

## The digest is a derived view, never stored authority

Following CAEM ADR 0008 and the N4 `N2_OUTCOME_DERIVATION` inventory: a carried
outcome is only a derived view and must exactly match recomputation, and hashes
are never semantic proof. There is therefore no digest field on the contract --
not a private one, not a cached one. `semantic_digest()` recomputes from current
state every time, so no stale value exists to be trusted, and
`dataclasses.replace` cannot carry one across.

A serialised carrier does hold a digest and derived labels. On the way back in
they are reconciled, never believed: parse strictly, rebuild the contract,
rederive, and require equality. A caller-supplied digest never gets to shape the
semantics it claims to describe.

## Golden vectors, because rederivation agrees with itself

Rederive-and-compare cannot notice a change in the canonicalisation itself: both
sides recompute with the same changed algorithm and agree. That is why
`semantic_projection_algorithm_id` is semantic *and* why the corpus pins exact
canonical bytes. A silent projection change under an unchanged algorithm id
breaks the pinned vector; a deliberate one is a visible edit to both.

## Canonicalisation is not reimplemented here

`app.common.strict_json` is this repository's shared fail-closed parser and
canonical-JSON implementation, and its module docstring is explicit that the
bare-hex family reproduces AgentReview's own `Sha256` form. A fourth
canonicaliser would be a fourth place to get duplicate-key, non-finite and
key-ordering policy subtly wrong. Types that JSON cannot hold directly are
projected into the supported value domain first, and the shared primitive is
called on the result.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from typing import ClassVar

from app.common.strict_json import (
    canonical_json_bytes,
    canonical_json_digest_hex,
    raw_bytes_digest_hex,
)

__all__ = [
    "CarrierReconciliationError",
    "DerivationStepV2",
    "EvidenceRequirementV2",
    "GeneratedEntrySpecV2",
    "SealedSgaqContractV2",
    "SemanticProjectionError",
    "canonical_semantic_bytes",
    "contract_id_for",
    "from_carrier",
    "profile_id_for",
    "schema_version_for",
    "semantic_digest",
    "semantic_projection",
    "to_carrier",
]


class SemanticProjectionError(AssertionError):
    """The projection was asked to describe something it cannot stand behind."""


class CarrierReconciliationError(AssertionError):
    """A serialised carrier did not reconcile against its own semantics."""


# --------------------------------------------------------------------------
# nested frozen records
# --------------------------------------------------------------------------


def _seal_int(value: object, field: str) -> int:
    """Exactly `int`. `bool` is a subclass of `int` and is refused on purpose:
    `True` and `1` are the same JSON-domain value under different intent."""
    if type(value) is not int:
        raise ValueError(f"{field}: expected exactly int, got {type(value).__name__}")
    return value


def _seal_bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{field}: expected exactly bool, got {type(value).__name__}")
    return value


def _seal_text(value: object, field: str) -> str:
    """Exactly `str`, and encodable.

    A lone surrogate is a perfectly constructible Python string that no UTF-8
    canonicalisation can serialise, so a contract carrying one would be
    constructible and have no identity at all. Refusing it here keeps
    "constructible" and "has an identity" the same set.
    """
    if type(value) is not str:
        raise ValueError(f"{field}: expected exactly str, got {type(value).__name__}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field}: is not encodable, so it can carry no identity") from exc
    return value


@dataclasses.dataclass(frozen=True, slots=True)
class _SemanticRecordV2:
    """Base for every record inside the semantic closure.

    `PROJECTED_FIELD_NAMES` and the dataclass fields are reconciled at import by
    `_assert_field_universe_is_total`, and `as_json_value` is built FROM that
    tuple. A field added to a record without being projected is therefore an
    import-time failure rather than a semantic change with an unchanged
    identity -- which is exactly what review found one level below the seal.
    """

    PROJECTED_FIELD_NAMES: ClassVar[tuple[str, ...]] = ()

    def as_json_value(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.PROJECTED_FIELD_NAMES}


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceRequirementV2(_SemanticRecordV2):
    """How much evidence a representation class requires before it may decide."""

    minimum_prefix_bytes: int = 0
    requires_full_content: bool = False

    PROJECTED_FIELD_NAMES: ClassVar[tuple[str, ...]] = (
        "minimum_prefix_bytes", "requires_full_content",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_prefix_bytes",
                           _seal_int(self.minimum_prefix_bytes, "minimum_prefix_bytes"))
        object.__setattr__(self, "requires_full_content",
                           _seal_bool(self.requires_full_content, "requires_full_content"))


@dataclasses.dataclass(frozen=True, slots=True)
class DerivationStepV2(_SemanticRecordV2):
    """One transformation the authority is permitted to perform locally."""

    operation: str = ""
    from_class: str = ""
    produces: str = ""

    PROJECTED_FIELD_NAMES: ClassVar[tuple[str, ...]] = ("operation", "from_class", "produces")

    def __post_init__(self) -> None:
        for name in self.PROJECTED_FIELD_NAMES:
            object.__setattr__(self, name, _seal_text(getattr(self, name), name))


@dataclasses.dataclass(frozen=True, slots=True)
class GeneratedEntrySpecV2(_SemanticRecordV2):
    """One entry the authority generates for itself, specified by CONTENT.

    `content_spec` exists because specifying an entry by presence is not
    specifying it: a store whose validity marker exists but is empty fails the
    directory test and sends a tool searching upward. S0B-M has not measured the
    real values, so nothing here may be populated with a plausible guess.
    """

    location: str = ""
    entry_kind: str = ""
    content_spec: str = ""

    PROJECTED_FIELD_NAMES: ClassVar[tuple[str, ...]] = ("location", "entry_kind", "content_spec")

    def __post_init__(self) -> None:
        for name in self.PROJECTED_FIELD_NAMES:
            object.__setattr__(self, name, _seal_text(getattr(self, name), name))



def _revalidating_setstate(self, state) -> None:
    """Make unpickling converge on the same validator as construction.

    `dataclasses` installs a public `__setstate__` on every frozen+slots class
    that rewrites fields with `object.__setattr__`, running neither `__init__`
    nor `__post_init__`. Since the module offers pickle as an acquisition path,
    that path has to reach the same guards as every other one -- otherwise a
    payload can mint a contract that construction would refuse and the
    exact-type gate still accepts.
    """
    if isinstance(state, Mapping):
        items = list(state.items())
    else:
        items = list(zip((f.name for f in dataclasses.fields(self)), state))
    for name, value in items:
        object.__setattr__(self, name, value)
    self.__post_init__()


#: The declared semantic type closure. Sealing is a property of this whole set,
#: not of the outermost type: review found every guard stopping at depth 0.
_SEMANTIC_RECORD_TYPES: tuple[type, ...] = (
    EvidenceRequirementV2, DerivationStepV2, GeneratedEntrySpecV2,
)

for _record in _SEMANTIC_RECORD_TYPES:
    _record.__setstate__ = _revalidating_setstate  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# normalisation helpers -- one place, before anything reads a field
# --------------------------------------------------------------------------


def _as_string_set(value: Iterable[str], field: str) -> frozenset[str]:
    """A mathematical set of strings, from an input that is actually one.

    `str` and `Mapping` are refused rather than iterated. Both were accepted and
    both were silent: a bare string explodes into its characters (a one-claim
    typo became seven single-character claims), and a mapping yields only its
    keys, so two contracts written to differ shared one identity.
    """
    if isinstance(value, (str, bytes)) or isinstance(value, Mapping):
        raise ValueError(
            f"{field}: expected a set or sequence of strings, got {type(value).__name__}"
        )
    items = [_seal_text(item, f"{field} member") for item in value]
    return frozenset(items)


def _as_keyed_relation(value: object, field: str, project) -> tuple[tuple[str, object], ...]:
    """A keyed relation, normalised to sorted pairs with duplicate keys refused."""
    if isinstance(value, Mapping):
        pairs = list(value.items())
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            if isinstance(item, (str, bytes)):
                raise ValueError(f"{field}: a pair may not be a string")
        pairs = [tuple(item) for item in value]  # type: ignore[misc]
    else:
        raise ValueError(f"{field}: expected a mapping or a sequence of pairs")
    keys = [key for key, _ in pairs]
    if not all(isinstance(key, str) for key in keys):
        raise ValueError(f"{field}: every key must be a string")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{field}: duplicate keys are refused")
    return tuple(sorted(((key, project(val, field)) for key, val in pairs), key=lambda p: p[0]))


def _as_ordered_unique(value: Iterable[object], field: str, kind: type) -> tuple[object, ...]:
    """An ordered sequence whose order is semantic and whose members are unique.

    The input must itself be ordered. A `set` handed to an ordered field froze
    hash-table order into the identity, so one logical contract produced a
    different digest in every process -- rederive-and-compare cannot survive
    that. The member check is exact rather than `isinstance`, matching the
    outer authority boundary: a subclass overriding `as_json_value` would
    otherwise define alternate semantics through virtual dispatch.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field}: an ordered sequence requires an ordered input")
    items = tuple(value)
    if not all(type(item) is kind for item in items):
        raise ValueError(f"{field}: every member must be exactly {kind.__name__}")
    if len(set(items)) != len(items):
        raise ValueError(f"{field}: duplicate members are refused")
    return items


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class SealedSgaqContractV2:
    """The semantic domain, fixed before any observation is interpreted.

    Recursively sealed: no caller-owned mutable object remains reachable after
    construction, so a caller holding the dict or set it passed in cannot widen
    or narrow the domain afterwards. `MappingProxyType` is deliberately not used
    -- it only wraps caller state, and it also breaks pickling, which would leave
    a verifying process unable to obtain the contract it needs.
    """

    # --- SEMANTIC -----------------------------------------------------------
    claims: frozenset[str]
    required_classes: tuple[tuple[str, frozenset[str]], ...]
    object_formats: frozenset[str]
    candidate_location_grammar: tuple[tuple[str, str], ...]
    evidence_requirements: tuple[tuple[str, EvidenceRequirementV2], ...]
    admissible_class_vocabulary: frozenset[str]
    permitted_derivations: tuple[DerivationStepV2, ...]
    verification_obligations: tuple[tuple[str, str], ...]
    generated_representation_spec: tuple[GeneratedEntrySpecV2, ...]
    toolchain_capability_requirements: frozenset[str]
    semantic_projection_algorithm_id: str
    # --- ENVELOPE -----------------------------------------------------------
    description: str = ""
    authoring_note: str = ""

    def __post_init__(self) -> None:
        seal = lambda name, value: object.__setattr__(self, name, value)  # noqa: E731

        seal("claims", _as_string_set(self.claims, "claims"))
        if not self.claims:
            raise ValueError("claims: a contract with no supported claim states nothing")

        seal("required_classes", _as_keyed_relation(
            self.required_classes, "required_classes",
            lambda v, f: _as_string_set(v, f)))
        seal("object_formats", _as_string_set(self.object_formats, "object_formats"))
        seal("candidate_location_grammar", _as_keyed_relation(
            self.candidate_location_grammar, "candidate_location_grammar",
            lambda v, f: _require_str(v, f)))
        seal("evidence_requirements", _as_keyed_relation(
            self.evidence_requirements, "evidence_requirements",
            lambda v, f: _require_record(v, f, EvidenceRequirementV2)))
        seal("admissible_class_vocabulary",
             _as_string_set(self.admissible_class_vocabulary, "admissible_class_vocabulary"))
        seal("permitted_derivations", _as_ordered_unique(
            self.permitted_derivations, "permitted_derivations", DerivationStepV2))
        seal("verification_obligations", _as_keyed_relation(
            self.verification_obligations, "verification_obligations",
            lambda v, f: _require_str(v, f)))
        seal("generated_representation_spec", _as_ordered_unique(
            self.generated_representation_spec, "generated_representation_spec",
            GeneratedEntrySpecV2))
        seal("toolchain_capability_requirements", _as_string_set(
            self.toolchain_capability_requirements, "toolchain_capability_requirements"))
        algorithm = self.semantic_projection_algorithm_id
        if not isinstance(algorithm, str) or not algorithm:
            raise ValueError("semantic_projection_algorithm_id: required, and it is semantic")
        if algorithm not in _PROJECTION_ALGORITHMS:
            raise ValueError(
                f"semantic_projection_algorithm_id: {algorithm!r} names no interpreter "
                f"this module implements; declaring one it does not have would let the "
                f"contract be projected by a different algorithm than it claims"
            )
        for label in ("description", "authoring_note"):
            if not isinstance(getattr(self, label), str):
                raise ValueError(f"{label}: envelope prose must be a string")


SealedSgaqContractV2.__setstate__ = _revalidating_setstate  # type: ignore[attr-defined]


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}: expected a string value")
    return value


def _require_record(value: object, field: str, kind: type) -> object:
    if type(value) is not kind:
        raise ValueError(f"{field}: expected exactly {kind.__name__}")
    return value


# --------------------------------------------------------------------------
# field classes, declared in production and reconciled in tests
# --------------------------------------------------------------------------

#: The production statement of which fields carry semantics. The tests hold an
#: INDEPENDENTLY written table and reconcile the two; neither is generated from
#: the other, because a universe derived by introspection would agree with any
#: mistake in the thing it introspects.
_SEMANTIC_FIELDS: tuple[str, ...] = (
    "claims",
    "required_classes",
    "object_formats",
    "candidate_location_grammar",
    "evidence_requirements",
    "admissible_class_vocabulary",
    "permitted_derivations",
    "verification_obligations",
    "generated_representation_spec",
    "toolchain_capability_requirements",
    "semantic_projection_algorithm_id",
)
_ENVELOPE_FIELDS: tuple[str, ...] = ("description", "authoring_note")


def _assert_field_universe_is_total() -> None:
    declared = set(_SEMANTIC_FIELDS) | set(_ENVELOPE_FIELDS)
    actual = {field.name for field in dataclasses.fields(SealedSgaqContractV2)}
    if declared != actual:
        raise SemanticProjectionError(
            f"field universe is not total: unclassified={sorted(actual - declared)} "
            f"stale={sorted(declared - actual)}"
        )
    if set(_SEMANTIC_FIELDS) & set(_ENVELOPE_FIELDS):
        raise SemanticProjectionError("a field is declared in two classes")
    # Depth 0 is not the domain. Every record reachable from a semantic field is
    # part of what the contract means, and review found every guard stopping at
    # the outer dataclass: a field added to a nested record was a semantic
    # change with an unchanged identity, invisible to the whole suite.
    for record in _SEMANTIC_RECORD_TYPES:
        declared = set(record.PROJECTED_FIELD_NAMES)
        actual = {f.name for f in dataclasses.fields(record)}
        if declared != actual:
            raise SemanticProjectionError(
                f"{record.__name__} is not total: unprojected={sorted(actual - declared)} "
                f"stale={sorted(declared - actual)}"
            )


_assert_field_universe_is_total()


# --------------------------------------------------------------------------
# projection and identity
# --------------------------------------------------------------------------

#: Bound INTO the projection, so a change of canonicalisation semantics changes
#: identity rather than silently reinterpreting existing contracts.
SEMANTIC_PROJECTION_ALGORITHM_V1 = "sgaq.semantic-projection.v1"

#: The registry that makes the declared interpreter load-bearing. Without it the
#: field was caller-chosen free text selecting no code path, so a contract could
#: declare `v2` and be projected by `v1` -- the exact failure the field was
#: introduced to prevent, in the one direction that matters.
_PROJECTION_ALGORITHMS: frozenset[str] = frozenset({SEMANTIC_PROJECTION_ALGORITHM_V1})

_SET_FIELDS = frozenset({
    "claims", "object_formats", "admissible_class_vocabulary",
    "toolchain_capability_requirements",
})
_ORDERED_FIELDS = frozenset({"permitted_derivations", "generated_representation_spec"})


def _project_value(field: str, value: object) -> object:
    if field in _SET_FIELDS:
        return sorted(value)  # type: ignore[arg-type]
    if field in _ORDERED_FIELDS:
        return [item.as_json_value() for item in value]  # type: ignore[union-attr]
    if field == "required_classes":
        return {key: sorted(val) for key, val in value}  # type: ignore[misc]
    if field == "evidence_requirements":
        return {key: val.as_json_value() for key, val in value}  # type: ignore[union-attr,misc]
    if field in ("verification_obligations", "candidate_location_grammar"):
        return dict(value)  # type: ignore[call-overload]
    return value


def semantic_projection(contract: SealedSgaqContractV2) -> dict[str, object]:
    """The closed semantic view of a contract, as JSON-domain values.

    Module-owned and non-virtual, and the type check is exact rather than
    `isinstance`: a subclass that overrode a projection method would otherwise
    define alternate semantics for an object every downstream check still
    accepts. Envelope fields are not read at all -- being absent from the
    projection is what makes them envelope.
    """
    if type(contract) is not SealedSgaqContractV2:
        raise SemanticProjectionError(
            f"exact SealedSgaqContractV2 required at an authority boundary, "
            f"got {type(contract).__name__}"
        )
    if contract.semantic_projection_algorithm_id not in _PROJECTION_ALGORITHMS:
        raise SemanticProjectionError(
            f"no interpreter for {contract.semantic_projection_algorithm_id!r}"
        )
    return {name: _project_value(name, getattr(contract, name)) for name in _SEMANTIC_FIELDS}


def canonical_semantic_bytes(contract: SealedSgaqContractV2) -> bytes:
    """Canonical bytes via the repository's shared primitive, never a local one."""
    return canonical_json_bytes(semantic_projection(contract))


def semantic_digest(contract: SealedSgaqContractV2) -> str:
    """Recomputed from current state on every call. There is nothing cached."""
    return canonical_json_digest_hex(semantic_projection(contract))


def contract_id_for(contract: SealedSgaqContractV2) -> str:
    return f"sgaq-contract-{semantic_digest(contract)[:16]}"


def profile_id_for(contract: SealedSgaqContractV2) -> str:
    return f"sgaq-profile-{semantic_digest(contract)[16:32]}"


def schema_version_for(contract: SealedSgaqContractV2) -> str:
    """Derived from the interpreter identity, not chosen by a caller."""
    algorithm = contract.semantic_projection_algorithm_id
    return f"sgaq-carrier-{raw_bytes_digest_hex(algorithm.encode('utf-8'))[:12]}"


# --------------------------------------------------------------------------
# internal serialisation boundary
# --------------------------------------------------------------------------

_CARRIER_FIELDS = frozenset({
    "semantic", "envelope", "contract_id", "profile_id", "schema_version", "semantic_digest",
})


def to_carrier(contract: SealedSgaqContractV2) -> dict[str, object]:
    """An internal carrier. Not a public schema, and not an authority.

    The digest and the labels travel as derived views so a reader can detect
    tampering; nothing downstream may believe them without rederiving.
    """
    return {
        "semantic": semantic_projection(contract),
        "envelope": {name: getattr(contract, name) for name in _ENVELOPE_FIELDS},
        "contract_id": contract_id_for(contract),
        "profile_id": profile_id_for(contract),
        "schema_version": schema_version_for(contract),
        "semantic_digest": semantic_digest(contract),
    }


def from_carrier(carrier: Mapping[str, object]) -> SealedSgaqContractV2:
    """Rebuild, rederive, reconcile. In that order, and never the reverse.

    The carried digest is checked against a digest computed from the rebuilt
    contract. A carrier that arrives with a digest describing content it does
    not hold is refused rather than trusted, and no caller-supplied value is
    ever used to decide what the semantics are.
    """
    present = set(carrier)
    if present != _CARRIER_FIELDS:
        raise CarrierReconciliationError(
            f"carrier fields do not match: unknown={sorted(present - _CARRIER_FIELDS)} "
            f"missing={sorted(_CARRIER_FIELDS - present)}"
        )
    semantic = carrier["semantic"]
    envelope = carrier["envelope"]
    if not isinstance(semantic, Mapping) or not isinstance(envelope, Mapping):
        raise CarrierReconciliationError("semantic and envelope must be objects")
    if set(semantic) != set(_SEMANTIC_FIELDS) or set(envelope) != set(_ENVELOPE_FIELDS):
        raise CarrierReconciliationError("carrier field sets do not match the contract")

    try:
        contract = SealedSgaqContractV2(
            claims=semantic["claims"],
            required_classes=semantic["required_classes"],
            object_formats=semantic["object_formats"],
            candidate_location_grammar=semantic["candidate_location_grammar"],
            evidence_requirements={
                key: EvidenceRequirementV2(**value)
                for key, value in semantic["evidence_requirements"].items()
            },
            admissible_class_vocabulary=semantic["admissible_class_vocabulary"],
            permitted_derivations=tuple(
                DerivationStepV2(**item) for item in semantic["permitted_derivations"]
            ),
            verification_obligations=semantic["verification_obligations"],
            generated_representation_spec=tuple(
                GeneratedEntrySpecV2(**item)
                for item in semantic["generated_representation_spec"]
            ),
            toolchain_capability_requirements=semantic["toolchain_capability_requirements"],
            semantic_projection_algorithm_id=semantic["semantic_projection_algorithm_id"],
            description=envelope["description"],
            authoring_note=envelope["authoring_note"],
        )
    except (TypeError, ValueError, AttributeError, KeyError) as exc:
        raise CarrierReconciliationError(f"carrier could not be rebuilt: {exc}") from exc

    expected_digest = semantic_digest(contract)
    if carrier["semantic_digest"] != expected_digest:
        raise CarrierReconciliationError("carried digest does not match the rederived digest")
    for label, derive in (
        ("contract_id", contract_id_for),
        ("profile_id", profile_id_for),
        ("schema_version", schema_version_for),
    ):
        if carrier[label] != derive(contract):
            raise CarrierReconciliationError(f"{label} is not its deterministic derivation")
    return contract
