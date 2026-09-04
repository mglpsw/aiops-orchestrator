"""Regression corpus for scripts/lint-canonical-ledger.py (#324).

Every fixture here is a MINIMAL synthetic ledger snippet containing exactly
one defect class, modelled directly on a real failure mode reproduced during
PR #318's three correction rounds and PR #325's own post-qualification
cleanup (see #324's issue body for the concrete evidence). Each test:

  1. asserts the defect fixture is flagged (RED reproduction of the real
     historical defect, not a synthetic invention);
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


def _rule_names(findings: list) -> set[str]:
    return {f.rule for f in findings}


# ---------------------------------------------------------------------------
# R1 -- DUPLICATE_LIFECYCLE_CLAIM
# ---------------------------------------------------------------------------

R1_DEFECT_FIXTURE = """\
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

R1_CORRECTED_FIXTURE = """\
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


def test_r1_duplicate_lifecycle_claim_reproduces_the_real_312_defect() -> None:
    result = lint_text(R1_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" in _rule_names(result.findings)


def test_r1_accepts_evidentiary_second_location_with_no_lifecycle_field() -> None:
    result = lint_text(R1_CORRECTED_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


def test_r1_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_occurrences: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_duplicate_lifecycle_claim", _noop)
    result = lint_text(R1_DEFECT_FIXTURE)
    assert "R1_DUPLICATE_LIFECYCLE_CLAIM" not in _rule_names(result.findings)


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
    assert sum(1 for f in result.findings if f.rule == "R2_STALE_LINE_LOCATOR") == 1  # one line, one finding


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
# R3 -- ORPHANED_CURRENT_SHA
# ---------------------------------------------------------------------------

R3_DEFECT_FIXTURE = """\
```yaml
issue_313:
  title: "example"
  final_head: 1f3f1b019492273cbd9963966ef975f732e4c57f
  codex_status: STALE_MUST_RERUN
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

R3_CORRECTED_FIXTURE_LABELLED_HISTORICAL = """\
```yaml
issue_313:
  title: "example"
  final_head: 1f3f1b019492273cbd9963966ef975f732e4c57f   # HISTORICAL predecessor head, superseded
```
"""


def test_r3_orphaned_current_sha_reproduces_the_real_313_defect() -> None:
    result = lint_text(R3_DEFECT_FIXTURE)
    assert "R3_ORPHANED_CURRENT_SHA" in _rule_names(result.findings)


def test_r3_accepts_forge_derived_current_field() -> None:
    result = lint_text(R3_CORRECTED_FIXTURE_FORGE_DERIVED)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result.findings)


def test_r3_accepts_a_sha_explicitly_labelled_historical() -> None:
    result = lint_text(R3_CORRECTED_FIXTURE_LABELLED_HISTORICAL)
    assert "R3_ORPHANED_CURRENT_SHA" not in _rule_names(result.findings)


def test_r3_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_blocks: object, _text: object, _result: object) -> None:
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

R5_CORRECTED_FIXTURE = """\
```yaml
issue_312:
  title: "example"
  implementation_status: complete
  disposition: FORGE_DERIVED
```
"""


def test_r5_internal_lifecycle_contradiction_reproduces_merged_issue_still_active() -> None:
    result = lint_text(R5_DEFECT_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings)


def test_r5_catches_the_reverse_contradiction_too() -> None:
    result = lint_text(R5_DEFECT_FIXTURE_REVERSE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" in _rule_names(result.findings)


def test_r5_accepts_consistent_or_forge_derived_lifecycle() -> None:
    result = lint_text(R5_CORRECTED_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result.findings)


def test_r5_mutation_disabling_the_rule_flips_the_defect_fixture_green(monkeypatch: pytest.MonkeyPatch) -> None:
    def _noop(_occurrences: object, _result: object) -> None:
        return None

    monkeypatch.setattr(lint_canonical_ledger, "_rule_internal_lifecycle_contradiction", _noop)
    result = lint_text(R5_DEFECT_FIXTURE)
    assert "R5_INTERNAL_LIFECYCLE_CONTRADICTION" not in _rule_names(result.findings)


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
    combined = R1_DEFECT_FIXTURE + "\n" + R2_DEFECT_FIXTURE + "\n" + R3_DEFECT_FIXTURE + "\n" + R4_DEFECT_FIXTURE + "\n" + R5_DEFECT_FIXTURE
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
