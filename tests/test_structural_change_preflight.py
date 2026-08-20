"""Fail-closed validation of the Structural Change Preflight's own normative
registry -- the machine-checkable half of *Authority-First Convergence Review*
(`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`).

## Why parsing a document is legitimate here, and was not in `#203-D0`

`#203-D0` failed three times because it parsed a **view** to recover a fact
that lived somewhere else -- forge canonicality, then runtime behaviour. The
document was not the authority; something else was, and the parse was an
inference across an authority boundary.

Here the relation is inverted. The preflight IS the authority: `PROJECT_OVERLAY.md`
names it the exclusive owner of the `STOP / REDESIGN` criteria. Reading its own
declared registry to check that registry's internal closure is not inference
across a boundary.

## What this suite deliberately does NOT do

It does not judge whether the method's English is faithful to its registry, and
it does not revalidate any forge observation. Historical `#242`/`#245`/`#246`/
`#247` facts are explanatory evidence recorded in prose; restating one here as
`assert findings == 0` would be two local copies agreeing, which establishes
nothing about the forge:

```text
ManualValue(0)  ∧  TestExpects(0)   ⇏   ForgeReviewFindings(0)
```

## Round-1 replacement (PR #248)

The first version of this suite was structurally wrong, and its own review
proved it against the exact head `9770a99524`. Each property below replaces a
mechanism rather than patching a leaf:

- evidence classes were enumerated in BOTH §5 and the registry, and this suite
  actively *required* that duplication. The registry is now the only closed
  enumeration and prose may not restate it.
- a single-valued `claim_status` plus a `forbidden_promotions` deny-list
  modelled these categories as one transition system. `#247` legitimately
  carries four of them at once, so one field was lossy -- and a deny-list over
  8 statuses left 48 of 56 ordered pairs implicitly permitted. Both concepts
  are retired; `PROVED` now requires positive admission from explicit proof.
- historical forge facts sat inside the normative registry. Method authority
  and method evidence are now separate: the method stays semantically complete
  if every historical locator becomes unavailable.
- the ownership check observed `docs/**/*.md` while claiming the repository.
  It now enumerates tracked Markdown with Git itself.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = REPO_ROOT / "docs" / "engineering" / "STRUCTURAL_CHANGE_PREFLIGHT.md"
PREFLIGHT_REL = "docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md"

REGISTRY_FORMAT_ID = "aiops.engineering.convergence-review-registry.v1"
_BEGIN = "<!-- BEGIN NORMATIVE: convergence-review-registry-v1 -->"
_END = "<!-- END NORMATIVE: convergence-review-registry-v1 -->"

#: Concepts retired by the round-1 structural replacement. Their presence is
#: not a stale name to rename -- it means the transition/evidence model they
#: belong to has come back.
RETIRED_REGISTRY_KEYS = frozenset({"claim_status", "forbidden_promotions", "corpus", "evidence_class"})

PROOF_PREDICATE = "PROVED"
PROOF_BASIS = "explicit_proof"


class RegistryError(Exception):
    pass


def _strict_json_loads(text: str) -> object:
    """Duplicate JSON keys are a silent last-one-wins collapse -- exactly the
    lossy-projection class this method exists to name."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict:
        seen: dict[str, object] = {}
        for key, value in pairs:
            if key in seen:
                raise RegistryError(f"duplicate JSON key {key!r} in the normative registry")
            seen[key] = value
        return seen

    return json.loads(text, object_pairs_hook=reject_duplicates)


def _split_registry(markdown: str) -> tuple[str, str]:
    """Returns (prose_outside_the_block, registry_json_text)."""

    begin_count, end_count = markdown.count(_BEGIN), markdown.count(_END)
    if begin_count != 1 or end_count != 1:
        raise RegistryError(
            f"expected exactly one registry marker pair, found begin={begin_count} end={end_count}"
        )
    begin_idx, end_idx = markdown.index(_BEGIN), markdown.index(_END)
    if end_idx < begin_idx:
        raise RegistryError("registry END marker precedes its BEGIN marker")

    region = markdown[begin_idx + len(_BEGIN) : end_idx]
    prose = markdown[:begin_idx] + markdown[end_idx + len(_END) :]

    fences = re.findall(r"```json\n(.*?)```", region, flags=re.DOTALL)
    if len(fences) != 1:
        raise RegistryError(f"expected exactly one fenced json block, found {len(fences)}")
    return prose, fences[0]


def _extract_registry(markdown: str) -> dict:
    doc = _strict_json_loads(_split_registry(markdown)[1])
    if not isinstance(doc, dict):
        raise RegistryError("registry is not a JSON object")
    return doc


@pytest.fixture(scope="module")
def markdown() -> str:
    return PREFLIGHT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def registry(markdown: str) -> dict:
    return _extract_registry(markdown)


@pytest.fixture(scope="module")
def prose(markdown: str) -> str:
    return _split_registry(markdown)[0]


# --- Shape, and the retired transition/evidence model --------------------


def test_registry_shape_is_closed(registry: dict) -> None:
    assert registry["format_id"] == REGISTRY_FORMAT_ID
    assert set(registry) == {
        "format_id",
        "convergence_boundaries",
        "non_coercions",
        "evidence_classes",
        "epistemic_predicates",
        "proof_admission",
        "discriminants",
    }


def test_retired_transition_and_corpus_concepts_do_not_return(registry: dict) -> None:
    """M1 / M4. `claim_status` and `forbidden_promotions` modelled these
    categories as one transition system; `corpus` put historical forge facts
    inside the method's own authority. Their return is a structural
    regression, not a naming slip."""

    present = RETIRED_REGISTRY_KEYS & set(registry)
    assert not present, f"retired registry concepts returned: {sorted(present)}"


def test_duplicate_registry_keys_are_rejected() -> None:
    with pytest.raises(RegistryError, match="duplicate JSON key"):
        _strict_json_loads('{"format_id": "a", "format_id": "b"}')


def test_reversed_registry_markers_are_rejected() -> None:
    with pytest.raises(RegistryError, match="precedes"):
        _extract_registry(f"{_END}\nprose\n{_BEGIN}\n```json\n{{}}\n```\n")


# --- Convergence boundaries ----------------------------------------------


def test_convergence_boundaries_are_declared_and_unique(registry: dict) -> None:
    boundaries = registry["convergence_boundaries"]
    assert boundaries, "the method must name the boundaries a recurrence is measured against"
    assert len(boundaries) == len(set(boundaries))
    assert all(isinstance(b, str) and b.strip() for b in boundaries)


# --- Non-coercions --------------------------------------------------------


def test_non_coercions_are_real_pairs(registry: dict) -> None:
    seen: set[tuple[str, str]] = set()
    for rule in registry["non_coercions"]:
        assert set(rule) == {"from", "to"}
        assert rule["from"] != rule["to"], rule
        pair = (rule["from"], rule["to"])
        assert pair not in seen, f"duplicate non-coercion {pair}"
        seen.add(pair)


# --- Evidence classes: exactly one closed enumeration --------------------


def test_evidence_classes_are_unique_and_non_empty(registry: dict) -> None:
    """M6."""

    classes = registry["evidence_classes"]
    assert classes, "the registry must own the evidence-class vocabulary"
    assert len(classes) == len(set(classes)), f"duplicate evidence class in {classes}"
    assert all(isinstance(c, str) and c.strip() for c in classes)


def test_prose_does_not_restate_the_evidence_class_vocabulary(registry: dict, prose: str) -> None:
    """F1, inverted. The previous suite REQUIRED §5 and the registry to carry
    the same four values, which is the duplicate-authority defect committed by
    the document that forbids it -- and it guarded only one direction, so a
    fifth term added to prose alone passed.

    Prose must reference the registry, never originate a second closed set."""

    leaked = sorted(c for c in registry["evidence_classes"] if c in prose)
    assert not leaked, (
        f"prose restates registry-owned evidence classes {leaked}; reference `evidence_classes` instead"
    )


# --- Epistemic predicates: typed, co-holding, no transition graph --------


def test_epistemic_predicates_are_unique_and_typed(registry: dict) -> None:
    """M5."""

    predicates = registry["epistemic_predicates"]
    assert predicates
    ids = [p["id"] for p in predicates]
    assert len(ids) == len(set(ids)), f"duplicate epistemic predicate id in {ids}"
    for p in predicates:
        assert set(p) == {"id", "basis"}, p
        assert p["id"].strip() and p["basis"].strip()


def test_epistemic_predicates_carry_no_ordering_or_transition_structure(registry: dict) -> None:
    """The categories are typed predicates, not states of one lifecycle. A
    rank, order, successor or transition field would reintroduce exactly the
    model `#247` falsified by carrying four of them simultaneously."""

    forbidden_fields = {"rank", "order", "level", "successor", "predecessor", "transitions", "promotes_to"}
    for p in registry["epistemic_predicates"]:
        assert not (forbidden_fields & set(p)), f"{p['id']} carries ordering structure: {p}"


def test_multiple_epistemic_predicates_can_co_hold(registry: dict) -> None:
    """POSITIVE CONTROL. `#247` is simultaneously mechanically verified,
    mutation discriminated, empirically supported and non-refuted. The model
    must be able to represent that conjunction without electing one of them as
    an exclusive status -- which the retired single-valued `claim_status`
    could not.

    This demonstrates representability only. It asserts nothing about the
    general algebra of these categories, which is an open formal question."""

    ids = {p["id"] for p in registry["epistemic_predicates"]}
    co_holding = {"MECHANICALLY_VERIFIED", "MUTATION_DISCRIMINATED", "EMPIRICALLY_SUPPORTED", "NON_REFUTED"}
    assert co_holding <= ids
    assert len(co_holding) > 1, "the control is vacuous unless several predicates are asserted together"
    # Nothing in the registry marks predicates mutually exclusive, so the
    # conjunction above is representable by construction.
    assert all(set(p) == {"id", "basis"} for p in registry["epistemic_predicates"])


# --- Positive proof admission, replacing the deny-list -------------------


def test_proof_admission_is_positive_and_explicit(registry: dict) -> None:
    """M2. The retired deny-list left 48 of 56 ordered pairs implicitly
    permitted; the reviewer found two of the gaps. Positive admission has one
    answer instead of N holes: `PROVED` is admissible only from explicit
    proof evidence."""

    admission = registry["proof_admission"]
    assert set(admission) == {"predicate", "basis", "required_evidence_fields", "insufficient_bases"}
    assert admission["predicate"] == PROOF_PREDICATE
    assert admission["basis"] == PROOF_BASIS

    by_id = {p["id"]: p["basis"] for p in registry["epistemic_predicates"]}
    assert by_id[PROOF_PREDICATE] == PROOF_BASIS, "PROVED must be admitted only by explicit proof"

    assert admission["required_evidence_fields"], "explicit proof must state what it must carry"
    assert len(set(admission["required_evidence_fields"])) == len(admission["required_evidence_fields"])


def test_no_other_basis_is_sufficient_for_proof(registry: dict) -> None:
    """Neither individually nor by accumulation. Every non-proof predicate's
    basis must be named insufficient, so adding a predicate cannot silently
    open a new route to `PROVED`."""

    admission = registry["proof_admission"]
    insufficient = set(admission["insufficient_bases"])
    assert PROOF_BASIS not in insufficient
    for p in registry["epistemic_predicates"]:
        if p["id"] == PROOF_PREDICATE:
            continue
        assert p["basis"] in insufficient, (
            f"{p['id']} basis {p['basis']!r} is not declared insufficient for proof"
        )


# --- Discriminants are method semantics, not evidence copies -------------


def test_discriminants_define_distinctions_without_embedding_evidence(registry: dict) -> None:
    """M7. A method rule can require a distinction without carrying the
    historical artifact that originally taught it -- that belongs to method
    evidence."""

    ids = [d["id"] for d in registry["discriminants"]]
    assert ids
    assert len(ids) == len(set(ids))
    for d in registry["discriminants"]:
        assert set(d) == {"id", "a", "b"}, f"{d['id']} carries fields beyond the distinction itself: {d}"
        assert d["a"] != d["b"], f"{d['id']} does not distinguish two different things"


# --- No historical forge fact inside the method authority ----------------


def test_normative_registry_carries_no_historical_forge_facts(registry: dict) -> None:
    """M4. Method authority must stay semantically complete if every historical
    locator becomes unavailable, so PR numbers, SHAs and review outcomes have
    no place in it. Checked structurally over the serialized registry rather
    than by reading prose."""

    serialized = json.dumps(registry)
    assert not re.search(r"\b[0-9a-f]{40}\b", serialized), "a commit SHA is embedded in the method authority"
    for field in ("pr", "source_commit", "squash_commit", "tree", "first_fresh_review_findings",
                  "corrective_commits_after_review"):
        assert f'"{field}"' not in serialized, f"historical evidence field {field!r} is in the method authority"


# --- Single ownership over the domain actually observed ------------------


def _tracked_markdown() -> list[str]:
    """ObservedDomain, enumerated by Git itself. The previous suite claimed the
    repository while observing `docs/**/*.md`, leaving 20 tracked Markdown
    files -- `AGENTS.md`, `README.md`, `.github/**` -- unscanned."""

    proc = subprocess.run(
        ["git", "ls-files", "--", "*.md"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in proc.stdout.splitlines() if line]


def test_observed_domain_covers_the_claimed_domain() -> None:
    """ClaimedDomain == ObservedDomain == tracked Markdown. Stated explicitly
    so the ownership assertion below cannot outrun what it inspects."""

    tracked = _tracked_markdown()
    assert tracked, "expected tracked Markdown files"
    assert PREFLIGHT_REL in tracked
    outside_docs = [p for p in tracked if not p.startswith("docs/")]
    assert outside_docs, (
        "the domain must extend beyond docs/ -- otherwise this check repeats the defect it replaces"
    )


def test_exactly_one_registry_owner_in_tracked_markdown() -> None:
    """M3."""

    owners = [
        path
        for path in _tracked_markdown()
        if _BEGIN in (REPO_ROOT / path).read_text(encoding="utf-8")
    ]
    assert owners == [PREFLIGHT_REL], f"the convergence-review registry must have one owner, found {owners}"
