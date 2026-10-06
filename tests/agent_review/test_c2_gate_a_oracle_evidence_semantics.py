"""Gate A evidence semantics (PR #372): what the legacy differential oracle may and may not claim.

E1  ExceptionTotalization must not imply RowSemanticMutation. The expectation for inputs on which the frozen
    baseline raises (`None + " "` in the pack-relevance text) is defined by a baseline whose ONLY change is that
    one expression. The projected row (`id`/`description` stay None), explicit-ref membership and selection are the
    baseline's own.
E2  The differential claim is qualified: legacy ADMITTED TYPED domain -> differential equivalence to 6bbd2f9;
    malformed / out-of-domain legacy inputs -> explicit, typed, non-conclusive fail-closed divergences (D1, D2).
    The S3 direct-call non-string member is defensive totalization (D3). The loss-label namespace residual (D4)
    is a diagnostic collision that can never suppress the required-loss consequence.

No runtime (`app/agent_review/**`) semantics are exercised here except through the production entry points.
"""

from __future__ import annotations

import copy
import difflib
import importlib.util
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from app.agent_review import chunk_payload_builder, payload_cost_model as pcm
from app.agent_review.schemas import ReviewIntake
from app.agent_review.semantic_chunker import build_semantic_chunk_plan
from tests.agent_review.test_c2_executable_contract_gate_a import _base_intake

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate-v1-c2-legacy-differential-oracle.py"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "v1_c2_legacy_differential_observations.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in FIXTURE["cases"]}


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("_v1_c2_evidence_generator", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _load_generator()
BASELINE_AVAILABLE = GEN.baseline_available()
needs_baseline = pytest.mark.skipif(not BASELINE_AVAILABLE, reason="frozen baseline commit not available (shallow clone)")


def _case_input(packs: list[Any], chunk_contracts: list[str], *, group: str = "primary_backend_logic", selected: str | None = None) -> dict[str, Any]:
    return {
        "target_profile": {"review_packs": {"packs": packs}},
        "chunk_files": ["elsewhere/a.txt"],
        "chunk_contracts": chunk_contracts,
        "selected_contract_pack": selected,
        "semantic_group": group,
        "chunk_id": "chunk-1",
    }


def _intake(profile: dict[str, Any]) -> ReviewIntake:
    return ReviewIntake.model_validate(
        {
            "schema_id": "agent-review.intake.v1",
            "schema_version": 1,
            "source": "aiops-review-intake",
            "target_repo": "example/target",
            "target_profile": profile,
            "artifacts": {},
            "artifact_status": [],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-10-05T00:00:00Z",
            "status": "complete",
        }
    )


# ---------------------------------------------------------------------------
# E1 -- totalization only removes the crash
# ---------------------------------------------------------------------------

W1 = "pack.relevance.baseline_raises.idless_x_empty_explicit_token"
W2 = "pack.relevance.baseline_raises.sibling_row_values"


def test_e1_totalized_expectation_keeps_frozen_rows_and_explicit_ref_membership() -> None:
    """Fixture-only (CI-safe). W1: an id-less pack is NOT referenced by the empty token `contract:` (None is not
    in {""}), so nothing is relevant; the old broad totalization turned None into "" and selected it with id "".
    W2: the sibling pack's row keeps `description: null`."""
    assert CASES[W1]["baseline_observation"] == {"outcome": "raises", "error": "TypeError"}
    assert CASES[W1]["totalized_baseline_observation"] == {"contracts": [], "not_relevant": True, "outcome": "ok", "packs": []}
    assert CASES[W2]["baseline_observation"] == {"outcome": "raises", "error": "TypeError"}
    assert CASES[W2]["totalized_baseline_observation"]["packs"] == [
        {"id": "alpha", "description": None, "recommended_review_preset": None}
    ]
    for case in CASES.values():
        if case["baseline_observation"]["outcome"] == "raises":
            for row in case["totalized_baseline_observation"]["packs"]:
                assert all(v != "" for v in row.values()), f"{case['id']}: a totalized row must never carry an empty-string carrier"


@needs_baseline
def test_e1_totalization_is_one_anchored_expression_and_nothing_else() -> None:
    source = GEN.load_baseline_source()
    totalized = GEN._totalized(source)
    diff = [line for line in difflib.unified_diff(source.splitlines(), totalized.splitlines(), lineterm="", n=0) if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert len(diff) == 2, diff
    removed, added = diff
    assert removed.startswith("-") and added.startswith("+")
    assert 'item.get("id", "") + " " + item.get("description", "")' in removed
    assert '(item.get("id") or "") + " " + (item.get("description") or "")' in added
    src_lines, tot_lines = source.splitlines(), totalized.splitlines()
    changed = [i for tag, i, _, _, _ in difflib.SequenceMatcher(None, src_lines, tot_lines, autojunk=False).get_opcodes() if tag != "equal"]
    assert len(changed) == 1 and src_lines.index("    filtered_packs = [") < changed[0], "the PACK seam, not the contract seam"
    # the row builder is byte-identical: the projection still leaves explicit None
    assert '"id": _clean_text(item.get("id")),' in totalized and '"id": _clean_text(item.get("id")) or ""' not in totalized


@needs_baseline
def test_e1_totalization_fails_closed_when_the_anchor_drifts() -> None:
    source = GEN.load_baseline_source()
    seam = GEN._RELEVANCE_SEAM
    drifted = {
        "seam_missing": source.replace(seam, "x", 1),
        "seam_duplicated": source + "\n" + seam + "\n",
        "pack_filter_missing": source.replace(GEN._PACK_FILTER_ANCHOR, "    other_filter = [", 1),
        "pack_filter_duplicated": source + "\n" + GEN._PACK_FILTER_ANCHOR + "\n",
    }
    for name, text in drifted.items():
        with pytest.raises(SystemExit, match="STOP_TOTALIZATION_ANCHOR_DRIFT"):
            GEN._totalized(text)
        assert name


NULL_COMBOS = list(itertools.product((None, "x"), (None, "x"), (None, "p")))  # id x description x preset


@needs_baseline
@pytest.mark.parametrize("combo", NULL_COMBOS, ids=lambda c: "/".join("null" if v is None else "val" for v in c))
def test_e1_row_before_totalization_equals_row_after_for_every_null_combination(combo: tuple[Any, Any, Any]) -> None:
    pack = {k: v for k, v in zip(("id", "description", "recommended_review_preset"), combo) if v is not None}
    source = GEN.load_baseline_source()
    baseline = GEN._module_from(source, "_e1_baseline_rows")
    totalized = GEN._module_from(GEN._totalized(source), "_e1_totalized_rows")
    case_input = _case_input([pack], ["target_profile:review_packs"], group="other")  # include_all: relevance never evaluated
    before, after = GEN.run_case(baseline, case_input), GEN.run_case(totalized, case_input)
    assert before == after
    expected = dict(zip(("id", "description", "recommended_review_preset"), combo))
    assert after["packs"] == [expected], "explicit nulls preserved exactly"


@needs_baseline
def test_e1_totalization_removes_the_typeerror_and_changes_nothing_where_the_baseline_completes() -> None:
    source = GEN.load_baseline_source()
    baseline = GEN._module_from(source, "_e1_baseline_all")
    totalized = GEN._module_from(GEN._totalized(source), "_e1_totalized_all")
    raised = completed = 0
    for case in FIXTURE["cases"]:
        before = GEN.run_case(baseline, case["input"])
        after = GEN.run_case(totalized, case["input"])
        if before["outcome"] == "raises":
            raised += 1
            assert after["outcome"] == "ok", f"{case['id']}: totalization must remove the TypeError"
            assert after == case["totalized_baseline_observation"]
        else:
            completed += 1
            assert after == before, f"{case['id']}: totalization changed an observation the baseline could complete"
    assert raised == 6 and completed == len(FIXTURE["cases"]) - 6
    assert {c["id"] for c in FIXTURE["cases"] if c["baseline_observation"]["outcome"] == "raises"} == {
        "pack.relevance.baseline_raises.id_only",
        "pack.relevance.baseline_raises.description_only",
        "pack.relevance.baseline_raises.preset_only",
        "pack.relevance.baseline_raises.all_null",
        W1,
        W2,
    }


@needs_baseline
def test_ab_e1_broad_totalization_is_caught_by_the_two_witnesses() -> None:
    """AB-E1-BROAD-TOTALIZATION: the previous `or ""` on the ROW BUILDER goes RED on W1 and W2."""
    source = GEN.load_baseline_source()
    anchor = (
        '                "id": _clean_text(item.get("id")),\n'
        '                "description": _clean_text(item.get("description")),\n'
        '                "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),'
    )
    assert source.count(anchor) == 1
    broad = source.replace(
        anchor,
        '                "id": _clean_text(item.get("id")) or "",\n'
        '                "description": _clean_text(item.get("description")) or "",\n'
        '                "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),',
    )
    broad_module = GEN._module_from(broad, "_e1_broad")
    good_module = GEN._module_from(GEN._totalized(source), "_e1_good")
    for case_id in (W1, W2):
        assert GEN.run_case(good_module, CASES[case_id]["input"]) == CASES[case_id]["totalized_baseline_observation"]
        assert GEN.run_case(broad_module, CASES[case_id]["input"]) != CASES[case_id]["totalized_baseline_observation"], case_id


# ---------------------------------------------------------------------------
# E2 -- qualified differential domain; D1 / D2 declared fail-closed divergences
# ---------------------------------------------------------------------------


def test_e2_fixture_qualifies_the_differential_claim_and_lists_d1_d2() -> None:
    domain = FIXTURE["differential_domain"]
    assert "ADMITTED TYPED domain" in domain["equivalence_domain"]
    declared = {d["id"]: d for d in domain["declared_fail_closed_divergences"]}
    assert set(declared) == {"D1.legacy_non_dict_pack_member", "D2.legacy_non_string_projected_fields"}
    for entry in declared.values():
        assert entry["disposition"] == "INTENTIONAL_FAIL_CLOSED_DIVERGENCE" and entry["authority"]
    assert "UNSUPPORTED_NONEMPTY" in declared["D1.legacy_non_dict_pack_member"]["current"]
    assert "MALFORMED_SHAPE" in declared["D2.legacy_non_string_projected_fields"]["current"]


D1_PACKS = {
    "text_member": [{"id": "a", "description": "d"}, "text"],
    "int_member": [{"id": "a", "description": "d"}, 3],
    "null_member": [{"id": "a", "description": "d"}, None],
    "list_member": [{"id": "a", "description": "d"}, ["x"]],
}
D2_PACKS = {
    "id_int": ([{"id": 123, "description": "d"}], "123"),
    "description_bool": ([{"id": "a", "description": True}], "True"),
    "preset_int": ([{"id": "a", "recommended_review_preset": 7}], "7"),
}


def _current(packs: list[Any], chunk_contracts: list[str]) -> tuple[dict[str, Any], list[str]]:
    return pcm.contracts_context(
        _intake({"review_packs": {"packs": copy.deepcopy(packs)}}),
        chunk_files=["elsewhere/a.txt"],
        chunk_contracts=chunk_contracts,
        chunk_id="chunk-1",
        selected_contract_pack=None,
        semantic_group="other",
    )


def _plan_for(packs: list[Any]) -> Any:
    intake = _base_intake()
    intake.target_profile = {"review_packs": {"packs": copy.deepcopy(packs)}}
    return build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)


def _assert_typed_non_conclusive(packs: list[Any], limitation: str) -> None:
    ctx, limits = _current(packs, ["target_profile:review_packs"])
    assert limitation in limits, "typed"
    assert ctx["review_packs"] == [], "no partial rows from an invalid source"
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in limits), "not 'not_relevant'"
    plan = _plan_for(packs)
    assert plan.status != "complete" and limitation in plan.limitations, "non-conclusive at the plan"
    valid = _plan_for([{"id": "a", "description": "d"}])
    assert valid.status == "complete" and not [lim for lim in valid.limitations if lim.startswith("invalid_source_")], "control"


@pytest.mark.parametrize("name", list(D1_PACKS))
def test_d1_non_dict_pack_member_is_an_intentional_typed_non_conclusive_fail_closed_divergence(name: str) -> None:
    _assert_typed_non_conclusive(D1_PACKS[name], "invalid_source_review_packs:UNSUPPORTED_NONEMPTY")


@needs_baseline
@pytest.mark.parametrize("name", list(D1_PACKS))
def test_d1_the_baseline_really_skips_the_member(name: str) -> None:
    baseline = GEN._module_from(GEN.load_baseline_source(), "_d1_baseline")
    observed = GEN.run_case(baseline, _case_input(D1_PACKS[name], ["target_profile:review_packs"], group="other"))
    assert observed["outcome"] == "ok" and [r["id"] for r in observed["packs"]] == ["a"], "baseline skips, current fails closed"


@pytest.mark.parametrize("name", list(D2_PACKS))
def test_d2_non_string_projected_field_is_an_intentional_typed_non_conclusive_fail_closed_divergence(name: str) -> None:
    _assert_typed_non_conclusive(D2_PACKS[name][0], "invalid_source_review_packs:MALFORMED_SHAPE")


@needs_baseline
@pytest.mark.parametrize("name", list(D2_PACKS))
def test_d2_the_baseline_really_stringifies_the_field(name: str) -> None:
    packs, stringified = D2_PACKS[name]
    baseline = GEN._module_from(GEN.load_baseline_source(), "_d2_baseline")
    observed = GEN.run_case(baseline, _case_input(packs, ["target_profile:review_packs"], group="other"))
    assert observed["outcome"] == "ok" and stringified in {v for row in observed["packs"] for v in row.values()}


# ---------------------------------------------------------------------------
# D3 -- S3 direct-call non-string member: defensive totalization
# ---------------------------------------------------------------------------


def test_d3_non_string_member_is_defensive_totalization_outside_the_producer_domain() -> None:
    """Precisely: the PRODUCER (`parse_contract_refs`) already excludes a non-string member and reports
    MALFORMED_MEMBER, so it never reaches `chunk_contracts`. Only a DIRECT call of `contracts_context` with a
    non-string `chunk_contracts` member used to raise AttributeError (predecessor); it now fails closed.
    The claim is limited to: runtime behavior unchanged on the admitted string-token domain."""
    refs, limits = pcm.parse_contract_refs(_intake({"contracts": [5, "contract:x"]}))
    assert refs == ["contract:x"] and "unresolved_contract_reference:MALFORMED_MEMBER" in limits
    ctx, limits = pcm.contracts_context(
        _intake({"domain_contracts": {"rules": [{"id": "x", "description": "d"}]}}),
        chunk_files=["elsewhere/a.txt"],
        chunk_contracts=[5],  # type: ignore[list-item]
        chunk_id="chunk-1",
        selected_contract_pack=None,
        semantic_group="other",
    )
    assert "unresolved_contract_reference:MALFORMED_MEMBER" in limits and ctx["domain_contracts"] == []
    assert pcm.classify_contract_ref_token(5).token_class == pcm.TOKEN_CLASS_INVALID


# ---------------------------------------------------------------------------
# D4 -- diagnostic loss-label namespace residual
# ---------------------------------------------------------------------------


def _shrink(ctx: dict[str, Any]) -> list[str]:
    payload: dict[str, Any] = {"chunk_context": {"contracts_context": ctx}, "limitations": []}
    for _ in range(40):
        if not chunk_payload_builder._shrink_contracts_context(payload):
            break
    return payload["limitations"]


def _required_pack_ctx(packs: list[dict[str, Any]]) -> dict[str, Any]:
    ctx, _ = pcm.contracts_context(
        _intake({"review_packs": {"packs": packs}}),
        chunk_files=["elsewhere/a.txt"],
        chunk_contracts=["target_profile:review_packs"],
        chunk_id="chunk-1",
        selected_contract_pack=None,
        semantic_group="other",
    )
    return pcm.clean_contracts_context_for_payload(ctx)


def test_d4_label_discriminates_within_the_synthetic_idless_domain() -> None:
    """Narrowed claim: injective over distinct id-less legacy PROJECTIONS (the synthetic label domain)."""
    projections = {
        tuple(sorted(pcm.legacy_pack_projection(dict(zip(pcm.LEGACY_PACK_PROJECTION_FIELDS, combo))).items(), key=lambda kv: kv[0]))
        for combo in itertools.product((None,), (None, "x", "y"), (None, "p", "q"))
    }
    labels = {pcm.unidentified_pack_loss_label(dict(p)) for p in projections}
    assert len(labels) == len(projections)


def test_d4_label_namespace_collision_is_possible_but_never_suppresses_the_required_loss() -> None:
    idless = {"recommended_review_preset": "a"}
    crafted_id = pcm.unidentified_pack_loss_label({"id": None, "description": None, "recommended_review_preset": "a"})
    colliding = _shrink(_required_pack_ctx([idless, {"id": crafted_id, "description": "crafted"}]))
    distinct = _shrink(_required_pack_ctx([idless, {"id": "other-id", "description": "plain"}]))
    # the residual: two lost required packs share one human-readable reason code (a crafted, identified pack id)
    assert len(distinct) == 2 and len(colliding) == 1
    # but the required loss is still emitted, typed, and is a required-context loss for the routing/gate predicate
    for limitations in (colliding, distinct):
        assert limitations and all(lim.startswith("required_contract_pack_context_lost:") for lim in limitations)
        assert all(pcm.is_required_context_loss(lim) for lim in limitations), "gate consequence unchanged"
    assert colliding[0] == f"required_contract_pack_context_lost:{crafted_id}"
