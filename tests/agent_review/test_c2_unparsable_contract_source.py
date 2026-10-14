"""AR-V1-C2 unparsable contract source: an existing-but-unparsable C2 source file is INVALID, never "no contract".

OBL-CL2-03: an absent/invalid required input is non-conclusive; invalid is never read as "no contract declared".
Before the fix `_load_optional_yaml` swallowed the YAML parse error, carried `None` and the plan stayed `complete`.
Every control starts from real files through the canonical producer (the R5 harness): load_repo_profile ->
build_intake -> build_semantic_chunk_plan -> build_chunk_payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agent_review import payload_cost_model as pcm, repo_profile
from app.agent_review.repo_profile import load_repo_profile
from tests.agent_review.test_c2_gate_a_r5_canonical_interfaces import BASE_CONTRACTS, _canonical_run, _write_repo

# kind -> (repo kwarg, source field, invalid-source limitation family, valid-yaml-but-malformed document)
SOURCES: dict[str, tuple[str, str, str, str]] = {
    "domain_contracts": ("contracts", "domain_contracts", "invalid_source_contract:MALFORMED_SHAPE", "rules: notalist\n"),
    "review_packs": ("packs", "review_packs", "invalid_source_review_packs:MALFORMED_SHAPE", "packs: {p1: notamapping}\n"),
}
UNPARSABLE_TEXTS: dict[str, str] = {
    "unterminated_flow_sequence": "rules: [unterminated\n",
    "nested_mapping_value": "a: b: c\n",
    "tab_indentation": "c1:\n\t- x\n",
    "unclosed_quote": "c1: 'open\n",
}
VALID_PACKS = {"packs": {"p1": {"domain_contract": "c1"}}}


def _kwargs(kind: str, text: Any) -> dict[str, Any]:
    """The source under test is `text`; the other C2 source is valid so a single fault is isolated."""
    # No pack may reference a contract when the contract source is the one under test: an emptied contract source
    # would otherwise also raise `required_source_absent` and mask which fault made the plan non-conclusive.
    kwarg = SOURCES[kind][0]
    return {kwarg: text, "contracts": BASE_CONTRACTS} if kwarg == "packs" else {kwarg: text}


def _emitted_contract_ids(payloads: dict[str, Any]) -> list[str]:
    return sorted(d["id"] for v in payloads.values() for d in v.chunk_context["contracts_context"]["domain_contracts"])


def _plan_limitations(plan: Any) -> list[str]:
    return list(plan.limitations)


def _is_critical(limitation: str) -> bool:
    return any(limitation.startswith(prefix) for prefix in pcm.CRITICAL_CONTRACT_LIMITATION_PREFIXES)


def _witness(tmp_path: Path, name: str, kind: str, text: str) -> tuple[str, bool]:
    """(plan.status, whether the critical invalid-source limitation reached the plan)."""
    _, plan, _ = _canonical_run(tmp_path, name, **_kwargs(kind, text))
    return plan.status, SOURCES[kind][2] in _plan_limitations(plan)


@pytest.mark.parametrize("variant", list(UNPARSABLE_TEXTS))
@pytest.mark.parametrize("kind", list(SOURCES))
def test_cm_unparsable_contract_source_is_invalid_not_absent(tmp_path: Path, kind: str, variant: str) -> None:
    _, field, family, _ = SOURCES[kind]
    text = UNPARSABLE_TEXTS[variant]
    # the fixtures really are unparsable: guard against a control that passes because the YAML was valid
    import yaml

    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(text)

    loaded = load_repo_profile(_write_repo(tmp_path, "load", **_kwargs(kind, text)), target_repo="example/target")
    assert loaded.status == "degraded" and loaded.error_class is None
    assert loaded.limitations == [f"{field}_yaml_invalid"]  # the legacy reason code is preserved

    intake, plan, payloads = _canonical_run(tmp_path, "run", **_kwargs(kind, text))
    assert f"{field}_yaml_invalid" in intake.target_profile["limitations"]
    assert plan.status != "complete", "an unparsable contract source must not stay conclusive"
    assert family in _plan_limitations(plan)
    assert _is_critical(family)
    # the carrier never surfaces as a declaration, and nothing from the unparsable source is emitted
    assert "__source_yaml_unparsable__" not in json.dumps([v.chunk_context for v in payloads.values()], sort_keys=True)
    if kind == "domain_contracts":
        assert _emitted_contract_ids(payloads) == []


@pytest.mark.parametrize("kind", list(SOURCES))
def test_pc_valid_contract_source_stays_conclusive(tmp_path: Path, kind: str) -> None:
    """Positive twin: the same real-file path with a parsable source is complete and carries its declaration."""
    text: Any = BASE_CONTRACTS if kind == "domain_contracts" else VALID_PACKS
    loaded = load_repo_profile(_write_repo(tmp_path, "load", **_kwargs(kind, text)), target_repo="example/target")
    assert loaded.status == "complete" and loaded.limitations == []
    intake, plan, payloads = _canonical_run(tmp_path, "run", **_kwargs(kind, text))
    assert intake.status == "complete" and plan.status == "complete"
    assert not any(_is_critical(lim) for lim in _plan_limitations(plan))
    assert _emitted_contract_ids(payloads) == ["c1"]


@pytest.mark.parametrize("kind", list(SOURCES))
def test_cm_unparsable_source_is_exactly_as_invalid_as_a_parsable_malformed_one(tmp_path: Path, kind: str) -> None:
    """Parity: the unparsable file is judged by the same grammar as a parsable-but-malformed one (no new status class)."""
    _, _, family, malformed = SOURCES[kind]
    unparsable = UNPARSABLE_TEXTS["unterminated_flow_sequence"]
    _, plan_a, payloads_a = _canonical_run(tmp_path, "unparsable", **_kwargs(kind, unparsable))
    _, plan_b, payloads_b = _canonical_run(tmp_path, "malformed", **_kwargs(kind, malformed))
    assert plan_a.status == plan_b.status != "complete"
    assert [lim for lim in plan_a.limitations if _is_critical(lim)] == [lim for lim in plan_b.limitations if _is_critical(lim)] == [family]
    assert _emitted_contract_ids(payloads_a) == _emitted_contract_ids(payloads_b)


def test_cm_unparsable_source_does_not_poison_the_other_valid_source(tmp_path: Path) -> None:
    """Only the broken source is invalid: the valid sibling is still loaded verbatim."""
    loaded = load_repo_profile(
        _write_repo(tmp_path, "mixed", **_kwargs("review_packs", UNPARSABLE_TEXTS["unterminated_flow_sequence"])), target_repo="example/target"
    )
    assert loaded.profile.domain_contracts == BASE_CONTRACTS
    assert loaded.limitations == ["review_packs_yaml_invalid"]


def test_cm_critical_prefix_tuple_is_not_widened_for_this_defect() -> None:
    """The fix rides the existing invalid_source_* families; the receipt-pinned tuple is deliberately untouched."""
    for prefix in ("invalid_source_contract:", "invalid_source_review_packs:"):
        assert pcm.CRITICAL_CONTRACT_LIMITATION_PREFIXES.count(prefix) == 1
    assert not any("yaml_invalid" in prefix for prefix in pcm.CRITICAL_CONTRACT_LIMITATION_PREFIXES)


def _swallowing_loader(path: Path, name: str, limitations: list[str]) -> Any:
    """The predecessor: the parse error is recorded as a limitation but the source is carried as None."""
    if not path.exists():
        return None
    loaded = repo_profile._load_yaml_file(path)
    if loaded.error_class:
        limitations.append(f"{name}_yaml_invalid")
        return None
    return loaded.data


@pytest.mark.parametrize("kind", list(SOURCES))
def test_ab_unparsable_source_carried_as_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    """AB-UNPARSABLE-SOURCE-AS-NONE: restoring the swallow makes the negative control go RED (plan complete)."""
    text = UNPARSABLE_TEXTS["unterminated_flow_sequence"]
    status, reached = _witness(tmp_path, "ab-before", kind, text)
    assert status != "complete" and reached is True
    with monkeypatch.context() as m:
        m.setattr(repo_profile, "_load_optional_yaml", _swallowing_loader)
        status, reached = _witness(tmp_path, "ab-mutant", kind, text)
        assert status == "complete" and reached is False, "mutant must go RED: unparsable is read as no contract"
    status, reached = _witness(tmp_path, "ab-after", kind, text)
    assert status != "complete" and reached is True


@pytest.mark.parametrize("kind", list(SOURCES))
def test_ab_unparsable_source_carrier_neutralised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    """AB-UNPARSABLE-SOURCE-CARRIER: a carrier the grammar accepts as a valid empty document also goes RED."""
    text = UNPARSABLE_TEXTS["unterminated_flow_sequence"]
    assert _witness(tmp_path, "abc-before", kind, text)[0] != "complete"
    with monkeypatch.context() as m:
        m.setitem(repo_profile._UNPARSABLE_SOURCE_CARRIERS, SOURCES[kind][1], {})
        status, reached = _witness(tmp_path, "abc-mutant", kind, text)
        assert status == "complete" and reached is False, "mutant must go RED"
    assert _witness(tmp_path, "abc-after", kind, text)[0] != "complete"
