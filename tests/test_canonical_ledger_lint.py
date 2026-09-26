"""Regression corpus for scripts/lint-canonical-ledger.py (#324).

Every fixture here is a MINIMAL synthetic ledger snippet containing exactly
one defect class, modelled directly on a real failure mode -- either from
PR #318's three correction rounds and PR #325's own post-qualification
cleanup, or (the R1(b)/R3/R4/R5/audit-live fixtures below) from native
Codex's and an independent adversarial lane's review of THIS linter's own
first cut, which reproduced concrete false negatives against realistic
ledger content. Each test:

  1. asserts the defect fixture is flagged (RED reproduction of a real
     failure mode, not a synthetic invention);
  2. asserts a corrected sibling fixture is NOT flagged (the fix the rule
     is meant to accept);
  3. mutation-tests the rule itself: with the rule function's effect
     disabled, the defect fixture must go GREEN -- proving the rule (not
     some other rule, and not the fixture's shape alone) is what
     discriminates it.

This mirrors the mutation-discipline this repository already applies to
production code (M1/M2/M3 style: mutate, prove RED becomes GREEN, restore).
Here the "mutation" is monkeypatching out one rule function at a time.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_SCRIPT_PATH = REPO_ROOT / "scripts" / "lint-canonical-ledger.py"

_spec = importlib.util.spec_from_file_location("lint_canonical_ledger", LINT_SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
lint_canonical_ledger = importlib.util.module_from_spec(_spec)
sys.modules["lint_canonical_ledger"] = lint_canonical_ledger
_spec.loader.exec_module(lint_canonical_ledger)

lint_text = lint_canonical_ledger.lint_text
LedgerParseError = lint_canonical_ledger.LedgerParseError


def _rule_names(findings: list) -> set[str]:
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# R1(a) -- DUPLICATE_LIFECYCLE_CLAIM, cross-block
# ---------------------------------------------------------------------------

R1A_DEFECT_FIXTURE = """\
# Section 1

```yaml
issue_312:
  title: "example"
  disposition: ACTIVE_PARALLEL
  implementation_status: not_started
```

# Section 5

```yaml
agentreview_property:
  - issue: 312
    caem_predecessor: "some design reference"
    current_status: OPEN_NOT_STARTED
```
"""

R1A_CORRECTED_FIXTURE = """\
# Section 1

```yaml
issue_312:
  title: "example"
  disposition: ACTIVE_PARALLEL
  implementation_status: not_started
```

# Section 5 -- evidentiary only, no lifecycle-claim field

```yaml
agentreview_property:
  - issue: 312
    caem_predecessor: "some design reference"
    predecessor_truth_maker: "some truth-maker"
```
"""

R1A_FORGE_DERIVED_SECOND_LOCATION_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  disposition: ACTIVE_PARALLEL
```

```yaml
agentreview_property:
  - issue: 312
    current_status: FORGE_DERIVED
```
"""


def test_r1a_duplicate_lifecycle_claim_reproduces_the_real_312_defect() -> None:
    result = lint_text(R1A_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" in _rule_names(result.findings)


def test_r1a_accepts_evidentiary_second_location_with_no_lifecycle_field() -> None:
    result = lint_text(R1A_CORRECTED_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


def test_r1a_does_not_flag_its_own_recommended_remediation() -> None:
    """Adversarial-lane finding: the rule's own advice ("make every location
    beyond the first FORGE_DERIVED") must not itself be a violation -- the
    first cut flagged it anyway because it never inspected field VALUES."""
    result = lint_text(R1A_FORGE_DERIVED_SECOND_LOCATION_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


def test_r1a_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_occurrences: object, _dupes: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_duplicate_lifecycle_claim", _noop)
    result = lint_text(R1A_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# R1(b) -- DUPLICATE_LIFECYCLE_CLAIM, same-block duplicate key
# (native Codex + independent adversarial lane finding against the first cut)
# ---------------------------------------------------------------------------

R1B_DEFECT_FIXTURE = """\
```yaml
issue_312:
  title: "first copy"
  disposition: ACTIVE_PARALLEL
  implementation_status: not_started
issue_312:
  title: "second copy, accidentally left after an edit"
  disposition: CLOSED_COMPLETED
  implementation_status: complete
```
"""

R1B_CORRECTED_FIXTURE = """\
```yaml
issue_312:
  title: "single copy"
  disposition: CLOSED_COMPLETED
  implementation_status: complete
```
"""


def test_r1b_same_block_duplicate_key_is_detected_via_compose_not_safe_load() -> None:
    """Reproduces the sharpest finding against the first cut: yaml.safe_load
    silently keeps only the LAST of two identical `issue_312:` keys in one
    mapping (Python dict semantics), so a check that only inspects the
    parsed result sees ONE clean block and reports nothing. Detected here
    via yaml.compose(), which preserves both key/value pairs as distinct
    nodes before construction discards the first."""
    result = lint_text(R1B_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" in _rule_names(result.findings)


def test_r1b_accepts_a_single_key() -> None:
    result = lint_text(R1B_CORRECTED_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


def test_r1b_mutation_disabling_compose_detection_flips_the_defect_fixture_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _empty(_raw_blocks: object) -> list[int]:
        return []

    monkeypatch.setattr(lint_canonical_ledger, "_find_same_block_duplicate_issue_keys", _empty)
    result = lint_text(R1B_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# Occurrence-walker robustness (adversarial-lane finding: shape-B parser
# silently dropped non-int issue numbers, including the real ledger's own
# `- issue: G5` row, and never recursed past depth 1)
# ---------------------------------------------------------------------------


def test_occurrence_walker_accepts_a_quoted_numeric_issue_id() -> None:
    text = """\
```yaml
agentreview_property:
  - issue: "312"
    disposition: ACTIVE_PARALLEL
```

```yaml
issue_312:
  disposition: CLOSED_COMPLETED
```
"""
    occurrences = lint_canonical_ledger._iter_issue_occurrences(
        lint_canonical_ledger._parse_yaml_blocks(lint_canonical_ledger._extract_yaml_blocks(text))
    )
    numbers = [n for n, _ in occurrences]
    assert numbers.count(312) == 2, f"expected the quoted '312' occurrence to be counted, got {numbers}"


def test_occurrence_walker_recurses_past_the_top_level() -> None:
    text = """\
```yaml
sections:
  section_5:
    - issue: 312
      disposition: ACTIVE_PARALLEL
```
"""
    occurrences = lint_canonical_ledger._iter_issue_occurrences(
        lint_canonical_ledger._parse_yaml_blocks(lint_canonical_ledger._extract_yaml_blocks(text))
    )
    assert any(n == 312 for n, _ in occurrences), "nested list occurrence must not be silently dropped"


def test_the_real_ledger_yields_at_least_twenty_occurrences() -> None:
    """Canary against silent parser degradation (adversarial-lane finding:
    a regression here would make every other rule's absence of a finding
    look identical to a clean document, with no signal that the parser
    itself stopped seeing most of the rows)."""
    ledger_path = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_CANONICAL_BACKLOG_AND_CAEM_REUSE_LEDGER.md"
    text = ledger_path.read_text()
    occurrences = lint_canonical_ledger._iter_issue_occurrences(
        lint_canonical_ledger._parse_yaml_blocks(lint_canonical_ledger._extract_yaml_blocks(text))
    )
    assert len(occurrences) >= 20, (
        f"only {len(occurrences)} occurrences found in the real ledger -- "
        "either the document shrank a lot, or the parser regressed"
    )


# ---------------------------------------------------------------------------
# R2 -- STALE_LINE_LOCATOR
# ---------------------------------------------------------------------------

R2_DEFECT_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  round_1: "hardened the commondir probe `:632` and the packed-refs probe `:935`"
```
"""

R2_CORRECTED_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  round_1: "hardened the commondir probe and the packed-refs probe"
```
"""


def test_r2_stale_line_locator_reproduces_the_real_312_defect() -> None:
    result = lint_text(R2_DEFECT_FIXTURE)
    assert "R2_STALE_LINE_LOCATOR" in _rule_names(result.findings)
    assert sum(1 for f in result.findings if f.rule == "R2_STALE_LINE_LOCATOR") == 1


def test_r2_accepts_semantic_site_names() -> None:
    result = lint_text(R2_CORRECTED_FIXTURE)
    assert "R2_STALE_LINE_LOCATOR" not in _rule_names(result.findings)


def test_r2_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_text: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_stale_line_locator", _noop)
    result = lint_text(R2_DEFECT_FIXTURE)
    assert "R2_STALE_LINE_LOCATOR" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# R3 -- ORPHANED_CURRENT_SHA (now structural, value-based)
# ---------------------------------------------------------------------------

R3_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example"
  final_head: 1f3f1b019492273cbd9963966ef975f732e4c57f
  codex_status: STALE_MUST_RERUN
```
"""

R3_FOLDED_SCALAR_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example -- folded scalar evasion, reproduced against the first cut's raw-text regex"
  final_head: >-
    1f3f1b019492273cbd9963966ef975f732e4c57f
```
"""

R3_LIST_VALUE_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example -- list-valued field evasion"
  final_head:
    - 1f3f1b019492273cbd9963966ef975f732e4c57f
```
"""

R3_UPPERCASE_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example -- uppercase hex evasion"
  final_head: "1F3F1B019492273CBD9963966EF975F732E4C57F"
```
"""

R3_FAKE_HISTORICAL_COMMENT_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example"
  final_head: 1f3f1b019492273cbd9963966ef975f732e4c57f   # not historical at all, still current
```
"""

R3_CORRECTED_FIXTURE_FORGE_DERIVED = """\
```yaml
issue_313:
  title: "example"
  final_head: FORGE_DERIVED
  codex_status: FORGE_DERIVED
```
"""

R3_NARRATIVE_FIELD_IS_NOT_IN_SCOPE_FIXTURE = """\
```yaml
issue_313:
  title: "example"
  final_head: FORGE_DERIVED
  correction_rounds:
    - round: 3
      finding: "fallback quorum on exact head 1f3f1b019492273cbd9963966ef975f732e4c57f"
```
"""


@pytest.mark.parametrize(
    "fixture",
    [
        R3_DEFECT_FIXTURE,
        R3_FOLDED_SCALAR_DEFECT_FIXTURE,
        R3_LIST_VALUE_DEFECT_FIXTURE,
        R3_UPPERCASE_DEFECT_FIXTURE,
        R3_FAKE_HISTORICAL_COMMENT_DEFECT_FIXTURE,
    ],
    ids=["plain", "folded_scalar", "list_value", "uppercase", "fake_historical_comment"],
)
def test_r3_orphaned_current_sha_reproduces_every_adversarial_evasion(fixture: str) -> None:
    result = lint_text(fixture)
    assert "R3_ORPHANED_CURRENT_SHA" in _rule_names(result.findings), fixture


def test_r3_accepts_forge_derived_current_field() -> None:
    result = lint_text(R3_CORRECTED_FIXTURE_FORGE_DERIVED)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result.findings)


def test_r3_does_not_scope_creep_into_narrative_evidence_fields() -> None:
    """A SHA inside `correction_rounds[].finding` is legitimate historical
    narrative -- not a CURRENT_AXIS_FIELD_NAMES key -- and must not be
    flagged; only the CURRENT_AXIS field itself (here correctly
    FORGE_DERIVED) is in scope."""
    result = lint_text(R3_NARRATIVE_FIELD_IS_NOT_IN_SCOPE_FIXTURE)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result.findings)


def test_r3_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_occurrences: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_orphaned_current_sha", _noop)
    result = lint_text(R3_DEFECT_FIXTURE)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# R4 -- UNQUALIFIED_EXACT_COUNT_CLAIM
# ---------------------------------------------------------------------------

R4_DEFECT_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  predecessor_falsifiers: "dropping O_NONBLOCK and disabling S_ISREG each independently kills exactly the 10 site witnesses, and 15 new tests total were added"
```
"""

R4_REALISTIC_PHRASING_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example -- the ledger's own realistic phrasing, missed by the first cut's narrow regex"
  mechanism_note: "RED->GREEN with 6 new witnesses incl. the end-to-end exploit; reverting to isinstance fails all 5 subclass witnesses"
```
"""

R4_CORRECTED_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  predecessor_falsifiers: "dropping O_NONBLOCK and disabling S_ISREG each have a discriminating witness; exact counts are recorded in the owning PR's own exact-head qualification comment"
```
"""


def test_r4_unqualified_exact_count_claim_reproduces_the_real_312_defect() -> None:
    result = lint_text(R4_DEFECT_FIXTURE)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" in _rule_names(result.findings)


def test_r4_catches_the_ledgers_own_realistic_digit_phrasing() -> None:
    result = lint_text(R4_REALISTIC_PHRASING_DEFECT_FIXTURE)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" in _rule_names(result.findings)


def test_r4_accepts_qualitative_witness_language() -> None:
    result = lint_text(R4_CORRECTED_FIXTURE)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" not in _rule_names(result.findings)


def test_r4_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_text: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_unqualified_exact_count", _noop)
    result = lint_text(R4_DEFECT_FIXTURE)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# R5 -- INTERNAL_LIFECYCLE_CONTRADICTION
# ---------------------------------------------------------------------------

R5_DEFECT_FIXTURE = """\
```yaml
issue_312:
  title: "example -- merged issue still showing ACTIVE"
  implementation_status: complete
  disposition: not_started
```
"""

R5_DEFECT_FIXTURE_REVERSE = """\
```yaml
issue_312:
  title: "example -- reverse contradiction"
  implementation_status: not_started
  current_status: INTEGRATED_COMPLETED
```
"""

R5_LEDGER_TAXONOMY_DEFECT_FIXTURE = """\
```yaml
issue_312:
  title: "example -- this ledger's OWN documented taxonomy value, missed by the first cut's small invented vocabulary"
  implementation_status: not_started
  disposition: CLOSED_COMPLETED
```
"""

R5_CORRECTED_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  implementation_status: complete
  disposition: FORGE_DERIVED
```
"""

R5_LEGITIMATE_IN_PROGRESS_FIXTURE = """\
```yaml
issue_312:
  title: "example -- neither complete nor not-started is a legitimate, non-contradictory state"
  implementation_status: not_started
  disposition: ACTIVE_PARALLEL
```
"""


def test_r5_internal_lifecycle_contradiction_reproduces_merged_issue_still_active() -> None:
    result = lint_text(R5_DEFECT_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings)


def test_r5_catches_the_reverse_contradiction_too() -> None:
    result = lint_text(R5_DEFECT_FIXTURE_REVERSE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings)


def test_r5_recognizes_this_ledgers_own_documented_taxonomy() -> None:
    result = lint_text(R5_LEDGER_TAXONOMY_DEFECT_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings)


def test_r5_accepts_consistent_or_forge_derived_lifecycle() -> None:
    result = lint_text(R5_CORRECTED_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result.findings)


def test_r5_does_not_false_positive_on_legitimate_in_progress_state() -> None:
    result = lint_text(R5_LEGITIMATE_IN_PROGRESS_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result.findings)


def test_r5_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_occurrences: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_internal_lifecycle_contradiction", _noop)
    result = lint_text(R5_DEFECT_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result.findings)


# ---------------------------------------------------------------------------
# Malformed YAML -- must fail closed with a clear message, never crash
# ---------------------------------------------------------------------------


def test_malformed_yaml_raises_a_typed_parse_error_not_a_raw_traceback() -> None:
    malformed = """\
```yaml
issue_312:
  title: "unterminated
```
"""
    with pytest.raises(LedgerParseError):
        lint_text(malformed)


def test_audit_live_reports_gate_unavailable_on_malformed_yaml_rather_than_crashing() -> None:
    malformed = """\
```yaml
issue_312:
  title: "unterminated
```
"""
    lines = lint_canonical_ledger.audit_live(malformed)
    assert any("GATE_UNAVAILABLE" in line for line in lines)


# ---------------------------------------------------------------------------
# --audit-live: FORGE_DERIVED sentinel must not be reported as drift
# ---------------------------------------------------------------------------


def test_audit_live_skips_forge_derived_sentinel_without_calling_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adversarial-lane finding against the first cut: a FORGE_DERIVED
    `disposition` was treated as "looks active" (no CLOSED/COMPLETED/...
    token in the string), so --audit-live reported false DRIFT against the
    very rows this PR just correctly rewrote to make no claim at all."""

    def _must_not_be_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("gh must not be invoked for a FORGE_DERIVED row")

    monkeypatch.setattr(lint_canonical_ledger.subprocess, "run", _must_not_be_called)
    text = """\
```yaml
issue_312:
  disposition: FORGE_DERIVED
```
"""
    lines = lint_canonical_ledger.audit_live(text)
    assert any("no independent lifecycle claim" in line for line in lines)
    assert not any("DRIFT" in line for line in lines)


# ---------------------------------------------------------------------------
# Combined / adversarial fixtures
# ---------------------------------------------------------------------------


def test_a_clean_ledger_produces_zero_findings() -> None:
    clean = """\
```yaml
issue_312:
  title: "example"
  implementation_status: complete
  disposition: FORGE_DERIVED
  current_qualification_source: "see PR #325's own exact-head qualification comment"
```

```yaml
agentreview_property:
  - issue: 312
    caem_predecessor: "mglpsw/caem ADR-0012, design reference only, authority_effect: none"
    predecessor_truth_maker: "retained capability, no-follow reopen"
```
"""
    result = lint_text(clean)
    assert result.ok, [f.render() for f in result.findings]


def test_multiple_defects_in_one_document_are_all_reported_independently() -> None:
    combined = (
        R1A_DEFECT_FIXTURE
        + "\n"
        + R2_DEFECT_FIXTURE
        + "\n"
        + R3_DEFECT_FIXTURE
        + "\n"
        + R4_DEFECT_FIXTURE
        + "\n"
        + R5_DEFECT_FIXTURE
    )
    result = lint_text(combined)
    names = _rule_names(result.findings)
    assert names == {
        "R1_DUPLICATE_LIFECYCLE_CLAIM",
        "R2_STALE_LINE_LOCATOR",
        "R3_ORPHANED_CURRENT_SHA",
        "R4_UNQUALIFIED_EXACT_COUNT_CLAIM",
        "R5_INTERNAL_LIFECYCLE_CONTRADICTION",
    }


def test_the_real_shipped_ledger_passes_check() -> None:
    """The actual committed ledger, not a fixture. This is the integration
    proof that the structural fix landed alongside the mechanism -- a
    linter with zero enforcement against its own real subject would be
    exactly the kind of unconvincing tooling this issue exists to avoid."""
    ledger_path = REPO_ROOT / "docs" / "checkpoints" / "AGENT_REVIEW_CANONICAL_BACKLOG_AND_CAEM_REUSE_LEDGER.md"
    text = ledger_path.read_text()
    result = lint_text(text)
    assert result.ok, "\n".join(f.render() for f in result.findings)


# ---------------------------------------------------------------------------
# GRANULAR mutation adequacy (native Codex P0 finding against the round-2
# cut): the tests above only prove each rule FUNCTION is present -- removing
# it entirely turns every fixture green. They do NOT prove any individual
# set member / regex alternative / vocabulary entry is load-bearing; an
# independent adversarial lane demonstrated 19 of 21 narrow mutations
# (one frozenset entry removed, one regex alternative deleted) survived
# undetected. Every block below closes exactly one such survivor: a
# dedicated fixture targeting ONE entry, proved RED against the real
# constant, then proved to flip GREEN when -- and only when -- that ONE
# entry is removed from the real constant (not the whole rule function).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", sorted(lint_canonical_ledger.LIFECYCLE_CLAIM_FIELD_NAMES))
def test_r1_every_lifecycle_field_name_is_independently_load_bearing(
    field_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = f"""\
```yaml
issue_999:
  title: "targets field {field_name}"
  {field_name}: some_value_a
```

```yaml
agentreview_property:
  - issue: 999
    {field_name}: some_value_b
```
"""
    result = lint_text(fixture)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" in _rule_names(result.findings), (
        f"fixture targeting {field_name!r} did not fire against the real field set"
    )

    narrowed = lint_canonical_ledger.LIFECYCLE_CLAIM_FIELD_NAMES - {field_name}
    monkeypatch.setattr(lint_canonical_ledger, "LIFECYCLE_CLAIM_FIELD_NAMES", narrowed)
    result_after_removal = lint_text(fixture)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result_after_removal.findings), (
        f"removing {field_name!r} from LIFECYCLE_CLAIM_FIELD_NAMES did not silence its own "
        "dedicated fixture -- some other field in the block is compensating, so this field "
        "is not actually independently load-bearing"
    )


@pytest.mark.parametrize("field_name", sorted(lint_canonical_ledger.CURRENT_AXIS_FIELD_NAMES))
def test_r3_every_current_axis_field_name_is_independently_load_bearing(
    field_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = f"""\
```yaml
issue_999:
  title: "targets field {field_name}"
  {field_name}: 1f3f1b019492273cbd9963966ef975f732e4c57f
```
"""
    result = lint_text(fixture)
    assert "R3_ORPHANED_CURRENT_SHA" in _rule_names(result.findings), (
        f"fixture targeting {field_name!r} did not fire against the real field set"
    )

    narrowed = lint_canonical_ledger.CURRENT_AXIS_FIELD_NAMES - {field_name}
    monkeypatch.setattr(lint_canonical_ledger, "CURRENT_AXIS_FIELD_NAMES", narrowed)
    result_after_removal = lint_text(fixture)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result_after_removal.findings), (
        f"removing {field_name!r} from CURRENT_AXIS_FIELD_NAMES did not silence its own "
        "dedicated fixture"
    )


@pytest.mark.parametrize("value", sorted(lint_canonical_ledger.COMPLETION_VALUES))
def test_r5_every_completion_value_is_independently_load_bearing(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = f"""\
```yaml
issue_999:
  title: "targets completion value {value}"
  implementation_status: not_started
  disposition: {value}
```
"""
    result = lint_text(fixture)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings), (
        f"fixture targeting completion value {value!r} did not fire"
    )

    narrowed = lint_canonical_ledger.COMPLETION_VALUES - {value}
    monkeypatch.setattr(lint_canonical_ledger, "COMPLETION_VALUES", narrowed)
    result_after_removal = lint_text(fixture)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result_after_removal.findings), (
        f"removing {value!r} from COMPLETION_VALUES did not silence its own dedicated fixture"
    )


@pytest.mark.parametrize("value", sorted(lint_canonical_ledger.NOT_STARTED_VALUES))
def test_r5_every_not_started_value_is_independently_load_bearing(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = f"""\
```yaml
issue_999:
  title: "targets not-started value {value}"
  implementation_status: complete
  disposition: {value}
```
"""
    result = lint_text(fixture)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings), (
        f"fixture targeting not-started value {value!r} did not fire"
    )

    narrowed = lint_canonical_ledger.NOT_STARTED_VALUES - {value}
    monkeypatch.setattr(lint_canonical_ledger, "NOT_STARTED_VALUES", narrowed)
    result_after_removal = lint_text(fixture)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result_after_removal.findings), (
        f"removing {value!r} from NOT_STARTED_VALUES did not silence its own dedicated fixture"
    )


# R4's regex has 6 independent alternatives (joined by `|`). A shared
# monkeypatch-a-narrower-regex helper proves each is load-bearing by
# rebuilding EXACT_COUNT_RE from all-but-one alternative and confirming
# that alternative's own dedicated fixture (and ONLY that fixture, to rule
# out cross-coverage from a different alternative) goes green.
# Test phrases are chosen to isolate exactly ONE alternative each -- an
# earlier draft used "kills exactly 10 witnesses" for kills_exactly and
# "exactly 10 witnesses" for exactly_digit_witness, and both turned out to
# ALSO match digit_witnesses (`\d+ witnesses` is a substring of both), so
# removing either alone left the fixture still red via the other branch.
# That failure was real signal, not a test bug: it proves digit_witnesses
# and kills_exactly/exactly_digit_witness genuinely overlap for those
# phrasings. The phrases below each exercise a shape only their own
# alternative recognizes (no digit+"witness(es)" substring for the
# kills/killed forms; a SINGULAR "1 witness" for exactly_digit_witness,
# which digit_witnesses' plural-only "witnesses" cannot match).
_R4_ALTERNATIVES: dict[str, tuple[str, str]] = {
    "kills_exactly": (r"\bkills?\s+exactly\s+(?:the\s+|all\s+)?\d+\b", "kills exactly 10 of the sites"),
    "killed_exactly": (r"\bkilled\s+exactly\s+(?:the\s+|all\s+)?\d+\b", "killed exactly the 6 mutants"),
    "digit_tests": (r"\b\d+\s+(?:new\s+)?tests?\b", "20 new tests added this round"),
    "digit_witnesses": (r"\b\d+\s+(?:new\s+)?witnesses\b", "6 new witnesses incl. the exploit"),
    "exactly_digit_witness": (
        r"\bexactly\s+(?:the\s+|all\s+)?\d+\s+witness(?:es)?\b",
        "exactly 1 witness catches it",
    ),
    "spelled_witnesses": (
        r"\b(?:ten|nine|eight|seven|six|five|four|three|two|one)\s+witnesses\b",
        "six witnesses reach the choke point",
    ),
}


@pytest.mark.parametrize("alt_name", sorted(_R4_ALTERNATIVES))
def test_r4_every_regex_alternative_is_independently_load_bearing(
    alt_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, phrase = _R4_ALTERNATIVES[alt_name]
    fixture = f"""\
```yaml
issue_999:
  title: "targets alternative {alt_name}: {phrase}"
```
"""
    result = lint_text(fixture)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" in _rule_names(result.findings), (
        f"fixture targeting alternative {alt_name!r} ({phrase!r}) did not fire against the real regex"
    )

    remaining_patterns = [pat for name, (pat, _phrase) in _R4_ALTERNATIVES.items() if name != alt_name]
    narrowed_re = re.compile("|".join(remaining_patterns), re.IGNORECASE)
    monkeypatch.setattr(lint_canonical_ledger, "EXACT_COUNT_RE", narrowed_re)
    result_after_removal = lint_text(fixture)
    assert "R4_UNQUALIFIED_EXACT_COUNT_CLAIM" not in _rule_names(result_after_removal.findings), (
        f"removing alternative {alt_name!r} did not silence its own dedicated fixture -- "
        "another alternative is compensating, so this one is not independently load-bearing"
    )


def test_r2_bare_locator_form_is_independently_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backtick form alone must not be what silently covers the bare
    form's own fixture -- the real historical defect was bare, never
    backtick-quoted."""
    fixture = """\
```yaml
issue_999:
  title: "commondir :632, packed-refs :935 -- the real historical shape, never backtick-quoted"
```
"""
    result = lint_text(fixture)
    assert "R2_STALE_LINE_LOCATOR" in _rule_names(result.findings)

    def _bare_form_disabled(_line: str) -> bool:
        return False

    monkeypatch.setattr(lint_canonical_ledger, "_line_has_bare_locator", _bare_form_disabled)
    result_after_removal = lint_text(fixture)
    assert "R2_STALE_LINE_LOCATOR" not in _rule_names(result_after_removal.findings), (
        "the bare-locator fixture is being covered by something other than the bare-locator "
        "detector itself -- it is not independently load-bearing"
    )


def test_r2_backtick_locator_form_is_independently_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = """\
```yaml
issue_999:
  title: "hardened the commondir probe `:632`"
```
"""
    result = lint_text(fixture)
    assert "R2_STALE_LINE_LOCATOR" in _rule_names(result.findings)

    monkeypatch.setattr(lint_canonical_ledger, "STALE_LOCATOR_RE", re.compile(r"(?!)"))  # never matches
    result_after_removal = lint_text(fixture)
    assert "R2_STALE_LINE_LOCATOR" not in _rule_names(result_after_removal.findings)


def test_r2_network_context_exclusion_is_load_bearing_and_does_not_over_suppress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the exclusion itself is real (not vacuously never triggered)
    by disabling it and confirming the bind-address false positive it
    exists to prevent would otherwise fire."""
    bind_address_fixture = """\
```yaml
issue_999:
  title: "the Router listens on `:8080`"
```
"""
    result = lint_text(bind_address_fixture)
    assert "R2_STALE_LINE_LOCATOR" not in _rule_names(result.findings), (
        "bind-address false positive: this must not fire with the exclusion active"
    )

    monkeypatch.setattr(lint_canonical_ledger, "_NETWORK_CONTEXT_RE", re.compile(r"(?!)"))
    result_with_exclusion_disabled = lint_text(bind_address_fixture)
    assert "R2_STALE_LINE_LOCATOR" in _rule_names(result_with_exclusion_disabled.findings), (
        "disabling the network-context exclusion should reveal that the underlying locator "
        "pattern WOULD have matched -- if it still doesn't fire, the exclusion test proves "
        "nothing"
    )


# ---------------------------------------------------------------------------
# CLI surface: main(), --check, --audit-live, exit codes (zero coverage
# before this review; verified manually by both reviewers, defended by
# neither -- adversarial-lane finding)
# ---------------------------------------------------------------------------


def test_main_check_mode_exit_code_clean(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.md"
    ledger.write_text("```yaml\nissue_999:\n  title: clean\n```\n")
    assert lint_canonical_ledger.main(["--check", str(ledger)]) == 0


def test_main_check_mode_exit_code_dirty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "ledger.md"
    ledger.write_text(R2_DEFECT_FIXTURE)
    assert lint_canonical_ledger.main(["--check", str(ledger)]) == 1
    captured = capsys.readouterr()
    assert "R2_STALE_LINE_LOCATOR" in captured.err


def test_main_check_mode_on_malformed_yaml_exits_nonzero_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.md"
    ledger.write_text('```yaml\nissue_312:\n  title: "unterminated\n```\n')
    exit_code = lint_canonical_ledger.main(["--check", str(ledger)])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "PARSE ERROR" in captured.err


def test_main_audit_live_mode_always_exits_zero_even_when_gh_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lint_canonical_ledger.shutil, "which", lambda _name: None)
    ledger = tmp_path / "ledger.md"
    ledger.write_text("```yaml\nissue_999:\n  disposition: ACTIVE_PARALLEL\n```\n")
    assert lint_canonical_ledger.main(["--audit-live", str(ledger)]) == 0


def test_main_requires_exactly_one_mode_flag() -> None:
    with pytest.raises(SystemExit):
        lint_canonical_ledger.main([])
    with pytest.raises(SystemExit):
        lint_canonical_ledger.main(["--check", "--audit-live"])


def test_main_missing_path_exits_with_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = lint_canonical_ledger.main(["--check", "/nonexistent/path/does/not/exist.md"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "does not exist" in captured.err
