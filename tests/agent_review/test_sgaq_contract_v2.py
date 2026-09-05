"""The sealed contract, attacked before it exists.

`#331`, SGAQ-S0B-A. This file closes exactly one proposition:

    P1_DOMAIN_IMMUTABILITY -- every field capable of altering the SGAQ semantic
    contract is recursively immutable, belongs to one explicit semantic
    projection, and any semantic change changes the rederived semantic identity
    or is rejected.

WHY THE FIELD ORACLE BELOW IS WRITTEN OUT BY HAND

The predecessor slice (PR #335, stopped) sealed one field of a multi-field
domain and shipped a guard test scoped to precisely that field. Round-two review
then performed the same prohibition through two neighbouring fields on an
unchanged contract object. A test universe derived by introspecting the
production dataclass would have agreed with that mistake, because it would have
enumerated whatever production happened to declare.

So `_ORACLE` below is written independently, by hand, and reconciled against
production. A production field that is not in this table is a RED test, not a
silently unclassified field. That reconciliation is the mechanism; the rest of
the file is consequences of it.

WHAT "SEMANTIC" MEANS HERE, AND WHY NAMES DECIDE NOTHING

A field is SEMANTIC iff changing it can change interpretation, admitted domain,
decision restriction, derivation, materialization semantics, or supported claim.
`semantic_projection_algorithm_id` is the case that makes the rule bite: it
carries a version-shaped name and is unambiguously semantic, because the same
field bytes read under a different projection mean something different.
"""

from __future__ import annotations

import copy
import dataclasses
import pickle

import pytest

from app.agent_review.sgaq_contract_v2 import (
    CarrierReconciliationError,
    DerivationStepV2,
    EvidenceRequirementV2,
    GeneratedEntrySpecV2,
    SealedSgaqContractV2,
    SemanticProjectionError,
    canonical_semantic_bytes,
    contract_id_for,
    profile_id_for,
    schema_version_for,
    semantic_digest,
    semantic_projection,
    to_carrier,
    from_carrier,
)

# --------------------------------------------------------------------------
# the independent field oracle -- written by hand, never introspected
# --------------------------------------------------------------------------

SEMANTIC = "SEMANTIC"
ENVELOPE = "ENVELOPE"

#: field -> (classification, justification). For ENVELOPE the justification must
#: state why mutating it cannot touch any of the six semantic axes (C7).
_ORACLE: dict[str, tuple[str, str]] = {
    "claims": (SEMANTIC, "is the supported-claim scope Q_B"),
    "required_classes": (SEMANTIC, "is R_B; alters the admitted domain"),
    "object_formats": (SEMANTIC, "alters admitted domain and materialization semantics"),
    "candidate_location_grammar": (SEMANTIC, "alters which locations may be interpreted"),
    "evidence_requirements": (SEMANTIC, "alters the decision restriction on evidence"),
    "admissible_class_vocabulary": (SEMANTIC, "alters the admitted domain"),
    "permitted_derivations": (SEMANTIC, "alters derivation"),
    "verification_obligations": (SEMANTIC, "alters verification/derivation semantics"),
    "generated_representation_spec": (SEMANTIC, "alters materialization semantics"),
    "toolchain_capability_requirements": (SEMANTIC, "alters interpretation"),
    "semantic_projection_algorithm_id": (
        SEMANTIC,
        "identifies the interpreter; identical field bytes under a different "
        "projection mean something different",
    ),
    "description": (
        ENVELOPE,
        "free presentation text; no code reads it, it enters no projection, and "
        "it cannot alter interpretation, admitted domain, decision restriction, "
        "derivation, materialization semantics or supported claim",
    ),
    "authoring_note": (
        ENVELOPE,
        "provenance prose for humans; same six-way justification as description",
    ),
}

#: Derived labels do NOT live on the contract -- storing one would create a
#: value that can go stale. They exist only on the serialized carrier and are
#: reconciled there, which is what C5 exercises.
_DERIVED_LABEL_CARRIER_FIELDS = frozenset({"contract_id", "profile_id", "schema_version"})


# --------------------------------------------------------------------------
# fixtures -- clearly synthetic; S0B-M has not measured real values
# --------------------------------------------------------------------------


def _contract(**overrides) -> SealedSgaqContractV2:
    """A structurally complete contract carrying deliberately synthetic values.

    `generated_representation_spec` in particular must NOT be populated with
    guessed real materialization bytes: S0B-M has not measured them, and a
    plausible-looking wrong value is worse than an obviously synthetic one.
    """
    base = dict(
        claims=frozenset({"resolve_exact_oid", "list_tree"}),
        required_classes={
            "resolve_exact_oid": frozenset({"class_a", "class_b"}),
            "list_tree": frozenset({"class_a"}),
        },
        object_formats=frozenset({"format_alpha"}),
        candidate_location_grammar=(
            ("container_template", "SYNTHETIC-container/{segment}"),
            ("identity_named_template", "SYNTHETIC-identity/{prefix}/{rest}"),
        ),
        evidence_requirements={
            "class_a": EvidenceRequirementV2(minimum_prefix_bytes=8, requires_full_content=False),
            "class_b": EvidenceRequirementV2(minimum_prefix_bytes=2, requires_full_content=True),
        },
        admissible_class_vocabulary=frozenset({"class_a", "class_b"}),
        permitted_derivations=(
            DerivationStepV2(operation="SYNTHETIC_derive_one", from_class="class_a",
                             produces="SYNTHETIC_artifact_one"),
            DerivationStepV2(operation="SYNTHETIC_derive_two", from_class="class_b",
                             produces="SYNTHETIC_artifact_two"),
        ),
        verification_obligations={
            "class_a": "SYNTHETIC: re-derive the identity the location claims",
            "class_b": "SYNTHETIC: verify internal consistency",
        },
        generated_representation_spec=(
            GeneratedEntrySpecV2(location="SYNTHETIC-entry-1", entry_kind="file",
                                 content_spec="SYNTHETIC-UNMEASURED-BYTES-1"),
            GeneratedEntrySpecV2(location="SYNTHETIC-entry-2", entry_kind="directory",
                                 content_spec=""),
        ),
        toolchain_capability_requirements=frozenset({"SYNTHETIC_capability_x"}),
        semantic_projection_algorithm_id="sgaq.semantic-projection.v1",
        description="synthetic contract for focused tests",
        authoring_note="written by the S0B-A corpus",
    )
    base.update(overrides)
    return SealedSgaqContractV2(**base)


# --------------------------------------------------------------------------
# C1-C7 -- the independent field oracle
# --------------------------------------------------------------------------


def test_c1_the_oracle_and_production_declare_the_same_field_universe() -> None:
    """Drift in either direction is a failure.

    A production field absent from the oracle is unclassified; an oracle field
    absent from production is a stale expectation. Both are RED.
    """
    actual = {field.name for field in dataclasses.fields(SealedSgaqContractV2)}
    assert set(_ORACLE) == actual, (
        f"oracle-only: {sorted(set(_ORACLE) - actual)}; "
        f"production-only (UNCLASSIFIED): {sorted(actual - set(_ORACLE))}"
    )


def test_c2_every_field_has_exactly_one_classification() -> None:
    for name, (classification, _) in _ORACLE.items():
        assert classification in (SEMANTIC, ENVELOPE), name


@pytest.mark.parametrize(
    "field_name", sorted(n for n, (c, _) in _ORACLE.items() if c == SEMANTIC)
)
def test_c3_every_semantic_field_participates_in_identity(field_name: str) -> None:
    """Vary exactly one semantic field: identity must change, or construction reject."""
    base = _contract()
    varied = _VARIATIONS[field_name]
    try:
        other = _contract(**{field_name: varied})
    except (ValueError, TypeError):
        return  # rejection is an acceptable outcome for this control
    assert semantic_digest(other) != semantic_digest(base), (
        f"{field_name} is declared SEMANTIC but does not participate in identity"
    )


@pytest.mark.parametrize(
    "field_name", sorted(n for n, (c, _) in _ORACLE.items() if c == ENVELOPE)
)
def test_c4_every_envelope_field_is_absent_from_identity(field_name: str) -> None:
    base = _contract()
    other = _contract(**{field_name: "a completely different envelope value"})
    assert semantic_digest(other) == semantic_digest(base), (
        f"{field_name} is declared ENVELOPE but altered the semantic identity"
    )


def test_c5_a_tampered_derived_label_is_rejected_at_the_carrier_boundary() -> None:
    contract = _contract()
    for label in sorted(_DERIVED_LABEL_CARRIER_FIELDS):
        carrier = dict(to_carrier(contract))
        carrier[label] = "attacker-selected-label"
        with pytest.raises(CarrierReconciliationError):
            from_carrier(carrier)


@pytest.mark.parametrize(
    "field_name",
    sorted(
        n for n, (c, _) in _ORACLE.items()
        if c == SEMANTIC and n in {
            "claims", "required_classes", "object_formats", "candidate_location_grammar",
            "evidence_requirements", "admissible_class_vocabulary", "permitted_derivations",
            "verification_obligations", "generated_representation_spec",
            "toolchain_capability_requirements",
        }
    ),
)
def test_c6_container_fields_do_not_retain_caller_state(field_name: str) -> None:
    """The alias attack, per container-bearing field rather than once."""
    mutable = _MUTABLE_INPUTS[field_name]()
    contract = _contract(**{field_name: mutable})
    before = semantic_digest(contract)
    _MUTATE_IN_PLACE[field_name](mutable)
    assert semantic_digest(contract) == before, (
        f"{field_name} retained caller-owned mutable state"
    )


def test_c7_every_envelope_field_records_its_six_axis_justification() -> None:
    for name, (classification, justification) in _ORACLE.items():
        if classification != ENVELOPE:
            continue
        assert len(justification) > 40, f"{name} lacks a recorded justification"


# --------------------------------------------------------------------------
# F1-F15 -- the required falsifiers
# --------------------------------------------------------------------------


def test_f1_replacing_a_semantic_field_changes_identity() -> None:
    base = _contract()
    other = dataclasses.replace(base, object_formats=frozenset({"format_beta"}))
    assert semantic_digest(other) != semantic_digest(base)


def test_f2_replacing_an_envelope_field_preserves_identity() -> None:
    base = _contract()
    other = dataclasses.replace(base, description="entirely different prose")
    assert semantic_digest(other) == semantic_digest(base)


def test_f3_a_tampered_carried_digest_is_rejected() -> None:
    carrier = dict(to_carrier(_contract()))
    carrier["semantic_digest"] = "0" * 64
    with pytest.raises(CarrierReconciliationError):
        from_carrier(carrier)


def test_f4_a_semantic_tamper_with_a_stale_carried_digest_is_rejected() -> None:
    """The carried digest is a derived view, so it cannot vouch for altered content."""
    carrier = dict(to_carrier(_contract()))
    semantic = dict(carrier["semantic"])
    semantic["object_formats"] = ["format_beta"]
    carrier["semantic"] = semantic
    with pytest.raises(CarrierReconciliationError):
        from_carrier(carrier)


def test_f5_a_tampered_label_is_rejected() -> None:
    carrier = dict(to_carrier(_contract()))
    carrier["contract_id"] = "sgaq-contract-0000000000000000"
    with pytest.raises(CarrierReconciliationError):
        from_carrier(carrier)


def test_f6_a_subclass_cannot_redefine_the_semantics() -> None:
    """Authority boundaries require the exact trusted type.

    A subclass overriding the projection would otherwise define alternate
    semantics for an object that still satisfies `isinstance`.
    """

    class Forged(SealedSgaqContractV2):
        pass

    forged = Forged(**{f.name: getattr(_contract(), f.name)
                       for f in dataclasses.fields(SealedSgaqContractV2)})
    with pytest.raises(SemanticProjectionError):
        semantic_projection(forged)
    with pytest.raises(SemanticProjectionError):
        semantic_digest(forged)


def test_f7_no_stored_digest_exists_to_become_authority() -> None:
    """Asserted structurally: there is no digest field to poison."""
    names = {field.name for field in dataclasses.fields(SealedSgaqContractV2)}
    assert not {n for n in names if "digest" in n or "identity" in n}
    assert not hasattr(_contract(), "__dict__") or "semantic_digest" not in vars(_contract())


def test_f8_the_golden_vector_pins_the_projection_behaviour() -> None:
    """A silent canonicalisation change under an unchanged algorithm id breaks this.

    Rederiving and comparing is self-consistent: both sides would recompute with
    the same changed algorithm and agree. Only a pinned external value notices.
    """
    contract = _contract()
    assert contract.semantic_projection_algorithm_id == "sgaq.semantic-projection.v1"
    assert canonical_semantic_bytes(contract) == _GOLDEN_BYTES
    assert semantic_digest(contract) == _GOLDEN_DIGEST


def test_f9_mutating_the_caller_input_after_construction_has_no_effect() -> None:
    claims = {"resolve_exact_oid", "list_tree"}
    contract = _contract(claims=claims)
    before = semantic_digest(contract)
    claims.add("a_claim_never_declared")
    assert semantic_digest(contract) == before


def test_f10_serialisation_round_trips_and_preserves_identity() -> None:
    contract = _contract()
    restored = from_carrier(to_carrier(contract))
    assert restored == contract
    assert semantic_digest(restored) == semantic_digest(contract)
    assert pickle.loads(pickle.dumps(contract)) == contract
    assert copy.deepcopy(contract) == contract


def test_f11_a_production_field_missing_from_the_oracle_is_red() -> None:
    """F11 is discharged by C1; this states the intent so the link is not lost."""
    actual = {field.name for field in dataclasses.fields(SealedSgaqContractV2)}
    assert set(_ORACLE) == actual


def test_f12_a_duplicate_member_of_a_semantic_set_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _contract(permitted_derivations=(
            DerivationStepV2(operation="d", from_class="class_a", produces="p"),
            DerivationStepV2(operation="d", from_class="class_a", produces="p"),
        ))


def test_f13_reordering_a_semantic_set_preserves_identity() -> None:
    """`claims` is declared a mathematical set, so caller order is not semantic."""
    one = _contract(claims=["resolve_exact_oid", "list_tree"])
    two = _contract(claims=["list_tree", "resolve_exact_oid"])
    assert semantic_digest(one) == semantic_digest(two)


def test_f14_reordering_a_semantic_sequence_changes_identity() -> None:
    """`permitted_derivations` is declared an ordered sequence, so order IS semantic."""
    base = _contract()
    reversed_steps = tuple(reversed(base.permitted_derivations))
    assert semantic_digest(_contract(permitted_derivations=reversed_steps)) != (
        semantic_digest(base)
    )


def test_f15_a_keyed_relation_is_identical_under_caller_key_order() -> None:
    forward = _contract(required_classes={
        "resolve_exact_oid": frozenset({"class_a", "class_b"}),
        "list_tree": frozenset({"class_a"}),
    })
    backward = _contract(required_classes={
        "list_tree": frozenset({"class_a"}),
        "resolve_exact_oid": frozenset({"class_a", "class_b"}),
    })
    assert semantic_digest(forward) == semantic_digest(backward)


# --------------------------------------------------------------------------
# collection-semantics declaration (section 8)
# --------------------------------------------------------------------------

_COLLECTION_SEMANTICS = {
    "claims": "set",
    "object_formats": "set",
    "admissible_class_vocabulary": "set",
    "toolchain_capability_requirements": "set",
    "required_classes": "keyed_relation",
    "evidence_requirements": "keyed_relation",
    "verification_obligations": "keyed_relation",
    "candidate_location_grammar": "keyed_relation",
    "permitted_derivations": "ordered_sequence",
    "generated_representation_spec": "ordered_sequence",
}


def test_every_semantic_collection_declares_its_set_semantics() -> None:
    """No collection may be left to whatever the implementation happened to do."""
    semantic_fields = {n for n, (c, _) in _ORACLE.items() if c == SEMANTIC}
    scalar = {"semantic_projection_algorithm_id"}
    assert set(_COLLECTION_SEMANTICS) == semantic_fields - scalar


def test_the_canonical_json_primitive_is_the_repository_one() -> None:
    """S0B-A must not add a fourth canonicaliser."""
    import app.agent_review.sgaq_contract_v2 as module

    source = __import__("inspect").getsource(module)
    assert "json.dumps" not in source, "a second canonicaliser was introduced"
    assert "canonical_json_bytes" in source and "canonical_json_digest_hex" in source


def test_derived_labels_are_deterministic_functions_of_semantics() -> None:
    contract = _contract()
    digest = semantic_digest(contract)
    assert contract_id_for(contract) == contract_id_for(_contract())
    assert profile_id_for(contract) == profile_id_for(_contract())
    assert digest[:8] in contract_id_for(contract)
    assert schema_version_for(contract) == schema_version_for(_contract())

    other = _contract(object_formats=frozenset({"format_beta"}))
    assert contract_id_for(other) != contract_id_for(contract)


def test_an_unknown_carrier_field_fails_closed() -> None:
    carrier = dict(to_carrier(_contract()))
    carrier["an_unexpected_field"] = 1
    with pytest.raises(CarrierReconciliationError):
        from_carrier(carrier)


def test_a_missing_carrier_field_fails_closed() -> None:
    carrier = dict(to_carrier(_contract()))
    del carrier["semantic"]
    with pytest.raises(CarrierReconciliationError):
        from_carrier(carrier)


# --------------------------------------------------------------------------
# variation and mutation tables used by the parametrized controls
# --------------------------------------------------------------------------

_VARIATIONS: dict[str, object] = {
    "claims": frozenset({"resolve_exact_oid"}),
    "required_classes": {"resolve_exact_oid": frozenset({"class_b"}),
                         "list_tree": frozenset({"class_a"})},
    "object_formats": frozenset({"format_beta"}),
    "candidate_location_grammar": (("container_template", "SYNTHETIC-other/{segment}"),),
    "evidence_requirements": {
        "class_a": EvidenceRequirementV2(minimum_prefix_bytes=99, requires_full_content=False),
        "class_b": EvidenceRequirementV2(minimum_prefix_bytes=2, requires_full_content=True),
    },
    "admissible_class_vocabulary": frozenset({"class_a"}),
    "permitted_derivations": (
        DerivationStepV2(operation="SYNTHETIC_derive_one", from_class="class_a",
                         produces="SYNTHETIC_artifact_changed"),
    ),
    "verification_obligations": {"class_a": "SYNTHETIC changed", "class_b": "SYNTHETIC other"},
    "generated_representation_spec": (
        GeneratedEntrySpecV2(location="SYNTHETIC-entry-1", entry_kind="file",
                             content_spec="SYNTHETIC-UNMEASURED-BYTES-CHANGED"),
    ),
    "toolchain_capability_requirements": frozenset({"SYNTHETIC_capability_y"}),
    "semantic_projection_algorithm_id": "sgaq.semantic-projection.v2-hypothetical",
}

_MUTABLE_INPUTS = {
    "claims": lambda: {"resolve_exact_oid", "list_tree"},
    "required_classes": lambda: {"resolve_exact_oid": {"class_a", "class_b"},
                                 "list_tree": {"class_a"}},
    "object_formats": lambda: {"format_alpha"},
    "candidate_location_grammar": lambda: [["container_template", "SYNTHETIC-container/{segment}"],
                                           ["identity_named_template", "SYNTHETIC-identity/{p}/{r}"]],
    "evidence_requirements": lambda: {
        "class_a": EvidenceRequirementV2(minimum_prefix_bytes=8, requires_full_content=False),
        "class_b": EvidenceRequirementV2(minimum_prefix_bytes=2, requires_full_content=True),
    },
    "admissible_class_vocabulary": lambda: {"class_a", "class_b"},
    "permitted_derivations": lambda: [
        DerivationStepV2(operation="SYNTHETIC_derive_one", from_class="class_a",
                         produces="SYNTHETIC_artifact_one"),
        DerivationStepV2(operation="SYNTHETIC_derive_two", from_class="class_b",
                         produces="SYNTHETIC_artifact_two"),
    ],
    "verification_obligations": lambda: {"class_a": "SYNTHETIC: a", "class_b": "SYNTHETIC: b"},
    "generated_representation_spec": lambda: [
        GeneratedEntrySpecV2(location="SYNTHETIC-entry-1", entry_kind="file",
                             content_spec="SYNTHETIC-UNMEASURED-BYTES-1"),
    ],
    "toolchain_capability_requirements": lambda: {"SYNTHETIC_capability_x"},
}


def _add_to_set(value):
    value.add("injected_after_construction")


def _add_to_mapping(value):
    value["injected_after_construction"] = value[next(iter(value))]


def _append_to_sequence(value):
    value.append(value[0])


_MUTATE_IN_PLACE = {
    "claims": _add_to_set,
    "required_classes": _add_to_mapping,
    "object_formats": _add_to_set,
    "candidate_location_grammar": _append_to_sequence,
    "evidence_requirements": _add_to_mapping,
    "admissible_class_vocabulary": _add_to_set,
    "permitted_derivations": _append_to_sequence,
    "verification_obligations": _add_to_mapping,
    "generated_representation_spec": _append_to_sequence,
    "toolchain_capability_requirements": _add_to_set,
}

#: Pinned in a later commit once the implementation exists; a placeholder here
#: would let F8 pass vacuously.
_GOLDEN_BYTES: bytes = b'{"admissible_class_vocabulary":["class_a","class_b"],"candidate_location_grammar":{"container_template":"SYNTHETIC-container/{segment}","identity_named_template":"SYNTHETIC-identity/{prefix}/{rest}"},"claims":["list_tree","resolve_exact_oid"],"evidence_requirements":{"class_a":{"minimum_prefix_bytes":8,"requires_full_content":false},"class_b":{"minimum_prefix_bytes":2,"requires_full_content":true}},"generated_representation_spec":[{"content_spec":"SYNTHETIC-UNMEASURED-BYTES-1","entry_kind":"file","location":"SYNTHETIC-entry-1"},{"content_spec":"","entry_kind":"directory","location":"SYNTHETIC-entry-2"}],"object_formats":["format_alpha"],"permitted_derivations":[{"from_class":"class_a","operation":"SYNTHETIC_derive_one","produces":"SYNTHETIC_artifact_one"},{"from_class":"class_b","operation":"SYNTHETIC_derive_two","produces":"SYNTHETIC_artifact_two"}],"required_classes":{"list_tree":["class_a"],"resolve_exact_oid":["class_a","class_b"]},"semantic_projection_algorithm_id":"sgaq.semantic-projection.v1","toolchain_capability_requirements":["SYNTHETIC_capability_x"],"verification_obligations":{"class_a":"SYNTHETIC: re-derive the identity the location claims","class_b":"SYNTHETIC: verify internal consistency"}}'
_GOLDEN_DIGEST: str = "8dff81d2d5f14462f96f37ff1cb7b50cf84bd2c7a0de8a1d6a2156cb4a8d0e29"
