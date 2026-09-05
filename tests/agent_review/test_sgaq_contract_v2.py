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
import sys
from pathlib import Path

import pytest

import app.agent_review.sgaq_contract_v2 as sgaq
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

#: Declared per collection, and reconciled against the oracle below. C6 is
#: parametrised over THIS rather than a hand-written witness list, so a new
#: container field cannot be silently uncovered.
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
        # Deliberately NOT in sorted order: the golden bytes must witness the
        # declared ordering, and a fixture already sorted lets an ordered->set
        # projection change pass the pin unnoticed.
        generated_representation_spec=(
            GeneratedEntrySpecV2(location="SYNTHETIC-entry-2", entry_kind="directory",
                                 content_spec=""),
            GeneratedEntrySpecV2(location="SYNTHETIC-entry-1", entry_kind="file",
                                 content_spec="SYNTHETIC-UNMEASURED-BYTES-1"),
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


@pytest.mark.parametrize("field_name", sorted(_COLLECTION_SEMANTICS))
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
# construction-time guards -- one falsifier per guard, not per witness
#
# The paired mutation proof found two production guards with no test reaching
# them at all, and one test that passed for the wrong reason. Closing the
# proposition means every guard that can refuse a contract has a case here.
# --------------------------------------------------------------------------


def test_the_production_field_class_table_must_cover_every_field() -> None:
    """A field in the dataclass but in neither class table is unclassified.

    This is production-internal drift, and it is NOT what C1 checks: C1
    reconciles the oracle against the dataclass, and stays green while
    production forgets to classify a field it already has.
    """
    original = sgaq._SEMANTIC_FIELDS
    try:
        sgaq._SEMANTIC_FIELDS = tuple(n for n in original if n != "object_formats")
        with pytest.raises(SemanticProjectionError, match="not total"):
            sgaq._assert_field_universe_is_total()
    finally:
        sgaq._SEMANTIC_FIELDS = original
    sgaq._assert_field_universe_is_total()


def test_the_totality_check_is_actually_wired_at_import(tmp_path) -> None:
    """The function working and the function being CALLED are different guards.

    A test that invokes `_assert_field_universe_is_total()` directly stays green
    when the module-level call is deleted. This imports a copy whose class table
    is incomplete, so only a wired check can refuse it.
    """
    import importlib.util

    source = Path(sgaq.__file__).read_text()
    broken = source.replace('    "object_formats",\n', "", 1)
    assert broken != source, "the field to drop was not found"
    module_path = tmp_path / "sgaq_contract_incomplete.py"
    module_path.write_text(broken)

    name = "sgaq_contract_incomplete"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # A slots=True dataclass re-creates its class and looks the module up in
    # sys.modules while doing so, so it has to be registered before exec.
    sys.modules[name] = module
    try:
        # The copy defines its OWN SemanticProjectionError, a different class
        # object from the imported one, so the type is asserted by name.
        with pytest.raises(Exception) as raised:
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    assert type(raised.value).__name__ == "SemanticProjectionError"
    assert "not total" in str(raised.value)


def test_a_field_declared_in_two_classes_is_refused() -> None:
    original = sgaq._ENVELOPE_FIELDS
    try:
        sgaq._ENVELOPE_FIELDS = original + ("claims",)
        with pytest.raises(SemanticProjectionError):
            sgaq._assert_field_universe_is_total()
    finally:
        sgaq._ENVELOPE_FIELDS = original


def test_a_keyed_relation_with_duplicate_keys_is_refused() -> None:
    """Distinct from F15, which is about key ORDER. Order-independence and
    duplicate rejection are different properties of a keyed relation."""
    with pytest.raises(ValueError, match="duplicate keys"):
        _contract(candidate_location_grammar=(
            ("container_template", "one"),
            ("container_template", "two"),
        ))


@pytest.mark.parametrize(
    ("label", "overrides", "match"),
    [
        ("non-string set member", {"claims": frozenset({"ok"}) | {1}}, "expected exactly str"),
        ("empty claim scope", {"claims": frozenset()}, "no supported claim"),
        ("non-string relation key", {"verification_obligations": ((1, "x"),)}, "must be a string"),
        ("relation of the wrong shape", {"verification_obligations": "not-a-relation"},
         "mapping or a sequence"),
        ("non-string relation value", {"verification_obligations": (("k", 2),)},
         "expected a string value"),
        ("wrong nested record type", {"evidence_requirements": {"class_a": "not-a-record"}},
         "expected exactly EvidenceRequirementV2"),
        ("wrong ordered member type", {"permitted_derivations": ("not-a-step",)},
         "exactly DerivationStepV2"),
        ("empty interpreter identity", {"semantic_projection_algorithm_id": ""}, "required"),
        ("non-string interpreter identity", {"semantic_projection_algorithm_id": 7}, "required"),
        ("non-string envelope prose", {"description": 3}, "must be a string"),
    ],
)
def test_construction_refuses_malformed_semantic_input(label, overrides, match) -> None:
    """Every construction-time guard, enumerated. A guard with no case here is a
    guard that could be deleted without any test noticing."""
    with pytest.raises(ValueError, match=match):
        _contract(**overrides)


def test_a_field_stored_by_reference_would_be_caught_by_the_alias_control() -> None:
    """The alias control must fail for the right reason.

    Copying the caller's iterable into a list already defeats a naive alias
    attack, so a normalisation that merely copies would pass C6 without
    sealing anything. This states the property C6 depends on: the stored value
    is immutable, not merely a copy.
    """
    contract = _contract()
    for name in _MUTABLE_INPUTS:
        stored = getattr(contract, name)
        assert isinstance(stored, (frozenset, tuple)), f"{name} is stored as {type(stored)}"


# --------------------------------------------------------------------------
# collection-semantics declaration (section 8)
# --------------------------------------------------------------------------



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
# the semantic type CLOSURE -- depth 0 is not the domain
#
# Review found every guard stopping at the outer dataclass: a field added to a
# nested record was a semantic change with an unchanged identity, and the whole
# suite stayed green. These controls are the oracle applied to the closure.
# --------------------------------------------------------------------------

#: Independently written, exactly like _ORACLE: record type name -> field names.
_RECORD_ORACLE: dict[str, frozenset[str]] = {
    "EvidenceRequirementV2": frozenset({"minimum_prefix_bytes", "requires_full_content"}),
    "DerivationStepV2": frozenset({"operation", "from_class", "produces"}),
    "GeneratedEntrySpecV2": frozenset({"location", "entry_kind", "content_spec"}),
}


def test_the_record_closure_is_declared_and_reconciled() -> None:
    actual = {record.__name__ for record in sgaq._SEMANTIC_RECORD_TYPES}
    assert set(_RECORD_ORACLE) == actual, "the declared record closure drifted"


@pytest.mark.parametrize("record_name", sorted(_RECORD_ORACLE))
def test_every_record_field_is_projected(record_name: str) -> None:
    record = next(r for r in sgaq._SEMANTIC_RECORD_TYPES if r.__name__ == record_name)
    assert {f.name for f in dataclasses.fields(record)} == _RECORD_ORACLE[record_name]
    assert set(record.PROJECTED_FIELD_NAMES) == _RECORD_ORACLE[record_name]


def test_a_record_field_left_out_of_the_projection_is_refused_at_import() -> None:
    """The closure equivalent of the outer totality check, and the finding that
    made it necessary: an unprojected record field altered behaviour without
    altering identity."""
    original = EvidenceRequirementV2.PROJECTED_FIELD_NAMES
    try:
        EvidenceRequirementV2.PROJECTED_FIELD_NAMES = ("minimum_prefix_bytes",)
        with pytest.raises(SemanticProjectionError, match="not total"):
            sgaq._assert_field_universe_is_total()
    finally:
        EvidenceRequirementV2.PROJECTED_FIELD_NAMES = original
    sgaq._assert_field_universe_is_total()


@pytest.mark.parametrize(
    ("label", "build", "match"),
    [
        ("mutable value inside a record",
         lambda: EvidenceRequirementV2(minimum_prefix_bytes=["8"], requires_full_content=False),
         "expected exactly int"),
        ("bool where int is declared",
         lambda: EvidenceRequirementV2(minimum_prefix_bytes=True, requires_full_content=False),
         "expected exactly int"),
        ("non-str in a text record field",
         lambda: DerivationStepV2(operation=1, from_class="a", produces="b"),
         "expected exactly str"),
        ("unencodable text in a record",
         lambda: GeneratedEntrySpecV2(location="\ud800", entry_kind="file", content_spec=""),
         "not encodable"),
    ],
)
def test_records_refuse_malformed_values(label, build, match) -> None:
    with pytest.raises(ValueError, match=match):
        build()


def test_a_record_subclass_cannot_enter_an_ordered_sequence(tmp_path) -> None:
    """Virtual dispatch at an authority boundary, one level down.

    A subclass overriding `as_json_value` would present innocuous content to the
    projection while the contract held something else -- three different
    contracts sharing one identity, measured.
    """
    class Widened(DerivationStepV2):
        pass

    with pytest.raises(ValueError, match="exactly DerivationStepV2"):
        _contract(permitted_derivations=(Widened(operation="a", from_class="b", produces="c"),))


def test_an_ordered_field_refuses_an_unordered_input() -> None:
    """A `set` handed to an ordered field froze hash-table order into the
    identity, so one logical contract had a different digest in every process."""
    with pytest.raises(ValueError, match="requires an ordered input"):
        _contract(permitted_derivations={
            DerivationStepV2(operation="a", from_class="b", produces="c")
        })


@pytest.mark.parametrize("field_name", ["claims", "object_formats"])
def test_a_set_field_refuses_inputs_that_iterate_into_something_else(field_name) -> None:
    """Both were silent: a bare string explodes into characters, and a mapping
    yields only its keys, so two contracts written to differ shared one identity."""
    with pytest.raises(ValueError, match="expected a set or sequence of strings"):
        _contract(**{field_name: "a_single_value"})
    with pytest.raises(ValueError, match="expected a set or sequence of strings"):
        _contract(**{field_name: {"a_single_value": "a value that would be dropped"}})


def test_unpickling_reaches_the_same_guards_as_construction() -> None:
    """`dataclasses` installs a __setstate__ that runs neither __init__ nor
    __post_init__, and this module offers pickle as an acquisition path."""
    good = _contract()
    state = {f.name: getattr(good, f.name) for f in dataclasses.fields(SealedSgaqContractV2)}
    state["semantic_projection_algorithm_id"] = ""
    victim = object.__new__(SealedSgaqContractV2)
    with pytest.raises(ValueError, match="required"):
        victim.__setstate__(state)


def test_an_interpreter_the_module_does_not_implement_is_refused() -> None:
    """The load-bearing direction: id => projection. Without it the field was
    caller-chosen text selecting no code path, so a contract could declare v2
    and be projected by v1."""
    with pytest.raises(ValueError, match="names no interpreter"):
        _contract(semantic_projection_algorithm_id="sgaq.semantic-projection.v2")


def test_the_declared_interpreter_is_inside_the_canonical_bytes() -> None:
    """With one registered interpreter, varying the id is necessarily a
    rejection rather than a different identity, so participation is asserted
    directly rather than through the C3 variation."""
    assert b"sgaq.semantic-projection.v1" in canonical_semantic_bytes(_contract())


@pytest.mark.parametrize("field_name", sorted(sgaq._ORDERED_FIELDS))
def test_every_ordered_field_witnesses_its_order(field_name: str) -> None:
    """F14 covered only one of the two ordered fields, so an ordered->set
    projection change on the other survived the whole suite."""
    base = _contract()
    reversed_members = tuple(reversed(getattr(base, field_name)))
    assert semantic_digest(_contract(**{field_name: reversed_members})) != semantic_digest(base)


_GOLDEN_CORPUS_IDS = ("ascii", "non_ascii", "empty_containers", "escapes")


def _corpus_contract(kind: str) -> SealedSgaqContractV2:
    if kind == "ascii":
        return _contract()
    if kind == "non_ascii":
        return _contract(claims=frozenset({"resolver_exato_ç", "árvore"}))
    if kind == "empty_containers":
        return _contract(object_formats=frozenset(), verification_obligations={},
                         toolchain_capability_requirements=frozenset())
    return _contract(description='"\\ and a \t tab',
                     claims=frozenset({'quote"backslash\\', "plain"}))


@pytest.mark.parametrize("kind", _GOLDEN_CORPUS_IDS)
def test_the_golden_corpus_pins_more_than_one_shape(kind: str) -> None:
    """One fixture is not a pin.

    Measured: with a single ASCII fixture, flipping `ensure_ascii` in the shared
    canonicaliser and turning an empty set into `null` both left all 62 tests
    green. The corpus adds the shapes that witness those changes.
    """
    assert semantic_digest(_corpus_contract(kind)) == _GOLDEN_CORPUS[kind]


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
_GOLDEN_BYTES: bytes = b'{"admissible_class_vocabulary":["class_a","class_b"],"candidate_location_grammar":{"container_template":"SYNTHETIC-container/{segment}","identity_named_template":"SYNTHETIC-identity/{prefix}/{rest}"},"claims":["list_tree","resolve_exact_oid"],"evidence_requirements":{"class_a":{"minimum_prefix_bytes":8,"requires_full_content":false},"class_b":{"minimum_prefix_bytes":2,"requires_full_content":true}},"generated_representation_spec":[{"content_spec":"","entry_kind":"directory","location":"SYNTHETIC-entry-2"},{"content_spec":"SYNTHETIC-UNMEASURED-BYTES-1","entry_kind":"file","location":"SYNTHETIC-entry-1"}],"object_formats":["format_alpha"],"permitted_derivations":[{"from_class":"class_a","operation":"SYNTHETIC_derive_one","produces":"SYNTHETIC_artifact_one"},{"from_class":"class_b","operation":"SYNTHETIC_derive_two","produces":"SYNTHETIC_artifact_two"}],"required_classes":{"list_tree":["class_a"],"resolve_exact_oid":["class_a","class_b"]},"semantic_projection_algorithm_id":"sgaq.semantic-projection.v1","toolchain_capability_requirements":["SYNTHETIC_capability_x"],"verification_obligations":{"class_a":"SYNTHETIC: re-derive the identity the location claims","class_b":"SYNTHETIC: verify internal consistency"}}'
_GOLDEN_CORPUS: dict[str, str] = {'ascii': '125ba27014b895d32e2399971481b7666fdf142d5c17bf8a66e72459216d165c', 'non_ascii': '86e12e60eec44a941a8f487ef90cbf310f663bf6f56c17a2b04dd47474479f19', 'empty_containers': '1304a1948cada9d9bd78e5aa49f8f085e9895f9759c960e965d08fa3268ff1f1', 'escapes': '622562c8ce5b89c8c02f5aba5c459c9204ed35204556baf814da385c45eb4ed9'}
_GOLDEN_DIGEST: str = "125ba27014b895d32e2399971481b7666fdf142d5c17bf8a66e72459216d165c"
