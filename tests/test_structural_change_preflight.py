"""Executable consumer of the Structural Change Preflight authority.

Topology, stated so it cannot drift:

    docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md   = MethodAuthority
    its normative machine-readable projection          = the finite decidable part
    this file                                          = Consumer(MethodAuthority)

This file is deliberately NOT a second methodology authority. It does not scan
prose for vocabulary, does not assert that a sentence exists, and does not carry
its own copy of the method's vocabulary. Everything it checks it reads from the
projection block, except five explicitly labelled STRUCTURAL PINS.

Those five pins are the projection's own shape claims, and pinning them is the
point: editing the projection to contradict the prose must fail here, which
forces the prose to be changed with it. A parallel authority would be this file
inventing rules the document does not state; these five are the document's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = REPO_ROOT / "docs" / "engineering" / "STRUCTURAL_CHANGE_PREFLIGHT.md"

_PROJECTION_RE = re.compile(r"```yaml\n(?P<body>.*?)\n```", re.DOTALL)


def _load_projection(text: str | None = None) -> dict:
    """The single normative projection carried by the authority document."""

    source = AUTHORITY.read_text(encoding="utf-8") if text is None else text
    blocks = []
    for match in _PROJECTION_RE.finditer(source):
        try:
            parsed = yaml.safe_load(match.group("body"))
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and "epistemic_predicates" in parsed:
            blocks.append(parsed)
    assert len(blocks) == 1, f"expected exactly one normative projection, found {len(blocks)}"
    return blocks[0]


def test_exactly_one_document_declares_itself_the_method_authority() -> None:
    """A second methodology authority is the failure this whole file guards."""

    declaring = []
    for candidate in (REPO_ROOT / "docs").rglob("*.md"):
        body = candidate.read_text(encoding="utf-8", errors="replace")
        for match in _PROJECTION_RE.finditer(body):
            try:
                parsed = yaml.safe_load(match.group("body"))
            except yaml.YAMLError:
                continue
            if isinstance(parsed, dict) and "epistemic_predicates" in parsed:
                declaring.append(candidate.relative_to(REPO_ROOT).as_posix())
    assert declaring == [AUTHORITY.relative_to(REPO_ROOT).as_posix()], declaring


def test_projection_names_its_authority_and_disclaims_being_one() -> None:
    projection = _load_projection()
    assert projection["authority_document"] == AUTHORITY.relative_to(REPO_ROOT).as_posix()
    assert projection["projection_of_prose"] is True
    assert projection["is_authority"] is False


def test_projection_is_internally_coherent() -> None:
    """Read from the projection; never restate its vocabulary here."""

    predicates = _load_projection()["epistemic_predicates"]
    members = predicates["members"]

    assert len(members) == len(set(members)), "duplicate predicate identity"
    assert members, "empty predicate domain"
    assert predicates["precondition"] in members
    excluded = set(predicates.get("excluded", {}))
    assert excluded.isdisjoint(members), sorted(excluded & set(members))
    for name, reason in predicates.get("excluded", {}).items():
        assert isinstance(reason, str) and reason.strip(), f"{name} excluded without a stated reason"


# --- the five STRUCTURAL PINS -------------------------------------------------
# Each is a shape claim the prose argues for at length. They are pinned so the
# projection cannot be edited into agreement with a model the document rejects.


def test_pin_predicates_are_unordered() -> None:
    """The document declines to settle an order; a ladder must not reappear."""

    predicates = _load_projection()["epistemic_predicates"]
    assert predicates["ordered"] is False
    forbidden_shapes = {"rank", "order", "levels", "promotions", "forbidden_promotions", "ladder"}
    assert forbidden_shapes.isdisjoint(predicates), sorted(forbidden_shapes & set(predicates))


def test_pin_predicates_may_co_hold() -> None:
    """A scalar status field drops information at the moment it is written."""

    assert _load_projection()["epistemic_predicates"]["may_co_hold"] is True


def test_pin_unavailable_is_not_a_predicate() -> None:
    """It is an outcome for an objective, not a predicate over a proposition."""

    predicates = _load_projection()["epistemic_predicates"]
    assert "UNAVAILABLE" not in predicates["members"]
    assert "UNAVAILABLE" in predicates.get("excluded", {})


def test_pin_proved_is_admitted_never_accumulated() -> None:
    proved = _load_projection()["proved_admission"]
    assert proved["admitted_by_accumulation"] is False
    assert proved["externality_required"] is True
    assert proved["required_basis"], "PROVED admitted with no stated basis"


def test_pin_semantic_evidence_never_composes_into_operational_authority() -> None:
    projection = _load_projection()
    assert projection["non_composition"]["semantic_evidence_to_operational_authority"] is False
    assert set(projection["authority_kinds"]) == {"semantic", "operational"}


# --- negative control ---------------------------------------------------------


def test_non_coercion_list_may_be_empty() -> None:
    """The universal rule is normative; the list is illustrative and open.

    A checker demanding a non-empty list would contradict the authority. This is
    the inverse of a vacuity guard and is deliberate.
    """

    non_coercions = _load_projection()["non_coercions"]
    assert non_coercions["list_is_illustrative_and_open"] is True
    assert non_coercions["rule"].strip()


def test_prose_wording_change_does_not_break_structural_enforcement() -> None:
    """Rewording prose must not fail these checks; only the projection may.

    This is the control that proves the enforcement is not a prose scanner.
    """

    original = AUTHORITY.read_text(encoding="utf-8")
    reworded = original.replace(
        "Everything above is the authority.",
        "The prose above is what governs; this paragraph was reworded.",
    )
    assert reworded != original, "control precondition changed; update this test deliberately"
    _load_projection(reworded)  # must still parse and validate
