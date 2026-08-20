"""Fail-closed validation of the Structural Change Preflight's own normative
registry -- the machine-checkable half of *Authority-First Convergence Review*
(`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`).

## Why parsing a document is legitimate here, and was not in `#203-D0`

`#203-D0` failed three times because it parsed a **view** to recover a fact
that lived somewhere else -- forge canonicality, then runtime behaviour. The
document was not the authority; something else was, and the parse was an
inference across an authority boundary.

Here the relation is inverted. The preflight IS the authority: it is a
normative process document, and `PROJECT_OVERLAY.md` names it the exclusive
owner of the `STOP / REDESIGN` criteria. Reading its own declared registry to
check that registry's internal closure is not inference across a boundary --
it is the same discipline the target-pack spec's `BEGIN NORMATIVE` block
already established, where structured normative content lives in a fenced
block and prose merely references it.

What this module therefore does NOT do, deliberately: scan prose for
vocabulary, count occurrences of words like "authority", or attempt to judge
whether the method's English is faithful to its registry. Those would be the
substring-scanner anti-pattern the method itself forbids.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = REPO_ROOT / "docs" / "engineering" / "STRUCTURAL_CHANGE_PREFLIGHT.md"

REGISTRY_FORMAT_ID = "aiops.engineering.convergence-review-registry.v1"
_BEGIN = "<!-- BEGIN NORMATIVE: convergence-review-registry-v1 -->"
_END = "<!-- END NORMATIVE: convergence-review-registry-v1 -->"

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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


def _extract_registry(markdown: str) -> dict:
    begin_count, end_count = markdown.count(_BEGIN), markdown.count(_END)
    if begin_count != 1 or end_count != 1:
        raise RegistryError(
            f"expected exactly one registry marker pair, found begin={begin_count} end={end_count}"
        )
    begin_idx, end_idx = markdown.index(_BEGIN), markdown.index(_END)
    if end_idx < begin_idx:
        raise RegistryError("registry END marker precedes its BEGIN marker")
    region = markdown[begin_idx + len(_BEGIN) : end_idx]

    fences = re.findall(r"```json\n(.*?)```", region, flags=re.DOTALL)
    if len(fences) != 1:
        raise RegistryError(f"expected exactly one fenced json block, found {len(fences)}")
    doc = _strict_json_loads(fences[0])
    if not isinstance(doc, dict):
        raise RegistryError("registry is not a JSON object")
    return doc


@pytest.fixture(scope="module")
def registry() -> dict:
    return _extract_registry(PREFLIGHT_PATH.read_text(encoding="utf-8"))


# --- Shape ---------------------------------------------------------------


def test_registry_shape_is_closed(registry: dict) -> None:
    assert registry["format_id"] == REGISTRY_FORMAT_ID
    assert set(registry) == {
        "format_id",
        "convergence_boundaries",
        "non_coercions",
        "evidence_class",
        "claim_status",
        "forbidden_promotions",
        "discriminants",
        "corpus",
    }


def test_duplicate_registry_keys_are_rejected() -> None:
    with pytest.raises(RegistryError, match="duplicate JSON key"):
        _strict_json_loads('{"format_id": "a", "format_id": "b"}')


def test_reversed_registry_markers_are_rejected() -> None:
    """Both markers unique but reversed: a naive split-and-take-first would
    silently accept a later unrelated fenced block."""

    with pytest.raises(RegistryError, match="precedes"):
        _extract_registry(f"{_END}\nprose\n{_BEGIN}\n```json\n{{}}\n```\n")


# --- Convergence boundaries ----------------------------------------------


def test_convergence_boundaries_are_declared_and_unique(registry: dict) -> None:
    boundaries = registry["convergence_boundaries"]
    assert boundaries, "the method must name the boundaries a recurrence is measured against"
    assert len(boundaries) == len(set(boundaries))
    assert all(isinstance(b, str) and b.strip() for b in boundaries)


def test_every_negative_corpus_entry_converged_on_a_declared_boundary(registry: dict) -> None:
    """A counterexample that converged on an unnamed boundary would mean the
    boundary vocabulary is incomplete -- the recurrence rule could not have
    fired for it."""

    boundaries = set(registry["convergence_boundaries"])
    for entry in registry["corpus"]["negative"]:
        assert entry["converged_on"] in boundaries, entry


# --- Non-coercions --------------------------------------------------------


def test_non_coercions_are_real_pairs(registry: dict) -> None:
    seen: set[tuple[str, str]] = set()
    for rule in registry["non_coercions"]:
        assert set(rule) == {"from", "to"}
        assert rule["from"] != rule["to"], rule
        pair = (rule["from"], rule["to"])
        assert pair not in seen, f"duplicate non-coercion {pair}"
        seen.add(pair)


# --- Epistemic status: two axes, no silent promotion ---------------------


def test_evidence_class_vocabulary_is_not_duplicated(registry: dict) -> None:
    """The method must reuse the evidence classes section 5 already
    established. Introducing a second, competing vocabulary would be the
    duplicate-authority defect committed by the document that forbids it."""

    assert registry["evidence_class"] == [
        "deterministic_complete",
        "finite_exhaustive",
        "empirically_supported",
        "advisory_observation",
    ]
    body = PREFLIGHT_PATH.read_text(encoding="utf-8")
    section_five = body.split("## 5. Evidence and mutation discrimination", 1)[1].split("\n## ", 1)[0]
    for term in registry["evidence_class"]:
        assert term in section_five, f"{term} is not the vocabulary section 5 established"


def test_claim_status_and_evidence_class_are_separate_axes(registry: dict) -> None:
    """They answer different questions -- how evidence was obtained, versus
    what may now be asserted -- so collapsing them into one ranking is the
    error the method warns about."""

    assert set(registry["claim_status"]).isdisjoint(set(registry["evidence_class"]))
    assert len(registry["claim_status"]) == len(set(registry["claim_status"]))


def test_forbidden_promotions_reference_only_declared_statuses(registry: dict) -> None:
    statuses = set(registry["claim_status"])
    for rule in registry["forbidden_promotions"]:
        assert set(rule) == {"from", "to"}
        assert rule["from"] in statuses, rule
        assert rule["to"] in statuses, rule
        assert rule["from"] != rule["to"], rule


def test_proved_is_unreachable_from_every_other_status(registry: dict) -> None:
    """The load-bearing prohibition: no accumulation of clean reviews,
    verified invariants, discriminated mutants or empirical support composes
    into a proof. Any status that could reach PROVED without an explicit
    forbidding rule would reopen exactly that door."""

    forbidden = {(r["from"], r["to"]) for r in registry["forbidden_promotions"]}
    for status in registry["claim_status"]:
        if status == "PROVED":
            continue
        assert (status, "PROVED") in forbidden or status in {"REFUTED", "UNAVAILABLE"}, (
            f"{status} -> PROVED is neither forbidden nor a terminal negative status"
        )


def test_clean_review_is_classified_non_refuted_not_proved(registry: dict) -> None:
    positive = registry["corpus"]["positive"]
    assert positive, "the positive corpus must record at least one case"
    for entry in positive:
        assert entry["claim_status"] == "NON_REFUTED", entry
        assert entry["claim_status"] != "PROVED"


# --- Discriminants --------------------------------------------------------


def test_discriminants_are_unique_and_actually_distinguish(registry: dict) -> None:
    ids = [d["id"] for d in registry["discriminants"]]
    assert len(ids) == len(set(ids))
    for d in registry["discriminants"]:
        assert set(d) == {"id", "a", "b", "evidence"}
        assert d["a"] != d["b"], f"{d['id']} does not distinguish two different things"


def test_every_discriminant_names_resolvable_evidence(registry: dict) -> None:
    """Referential integrity of the method's own evidence. A methodology that
    cites a deleted artifact has silently degraded, which is the
    exact-subject-evidence rule applied to this document itself.

    `forge_record` evidence is recorded but deliberately NOT resolved: this
    suite is offline, and pretending to verify a forge fact here would be the
    coercion the registry's own `non_coercions` forbid."""

    for d in registry["discriminants"]:
        evidence = d["evidence"]
        assert set(evidence) == {"kind", "value"}
        assert evidence["kind"] in {"repository_path", "forge_record"}, d
        if evidence["kind"] == "repository_path":
            assert (REPO_ROOT / evidence["value"]).exists(), (
                f"{d['id']} cites {evidence['value']}, which does not exist"
            )


# --- Corpus ---------------------------------------------------------------


def test_positive_corpus_records_well_formed_identities(registry: dict) -> None:
    """Source, squash and tree are recorded as distinct identities. The test
    asserts their FORM only -- it cannot confirm the forge relation offline,
    and says so rather than implying it did."""

    for entry in registry["corpus"]["positive"]:
        for field in ("source_commit", "squash_commit", "tree"):
            assert _SHA_RE.fullmatch(entry[field]), f"{field} is not a 40-hex sha: {entry[field]!r}"
        assert entry["source_commit"] != entry["squash_commit"], (
            "a squash merge produces a DIFFERENT commit; recording them as equal "
            "would collapse commit identity into content identity"
        )
        assert entry["corrective_commits_after_review"] == 0


def test_negative_corpus_is_non_empty_and_distinct_from_positive(registry: dict) -> None:
    """The method is extracted from a contrast. A corpus with no
    counterexamples would make the positive case unfalsifiable."""

    negative = {e["pr"] for e in registry["corpus"]["negative"]}
    positive = {e["pr"] for e in registry["corpus"]["positive"]}
    assert negative, "the method must retain its counterexamples"
    assert negative.isdisjoint(positive)


# --- Single ownership -----------------------------------------------------


def test_stop_redesign_criteria_have_exactly_one_owning_document() -> None:
    """`PROJECT_OVERLAY.md` declares this document the exclusive owner of the
    STOP/REDESIGN criteria, thresholds and procedure. This checks the exact
    typed artifact -- a second normative registry block -- rather than
    inferring ownership from prose."""

    owners = [
        path
        for path in sorted(REPO_ROOT.glob("docs/**/*.md"))
        if _BEGIN in path.read_text(encoding="utf-8")
    ]
    assert owners == [PREFLIGHT_PATH], f"convergence-review registry must have one owner, found {owners}"
