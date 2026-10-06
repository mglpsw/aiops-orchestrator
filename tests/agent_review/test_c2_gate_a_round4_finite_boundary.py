"""Gate A Round-4 finite-boundary closure (PR #372, independent review S1/S2/S3).

S1  LegacyPackItemIsDict -> LegacyPackRowExists: every dict item of a legacy pack list is ONE row of the exact
    baseline three-field projection {id, description, recommended_review_preset} (nulls kept), whatever subset of
    the carriers is non-null. The required floor and the loss label derive from that projection; no carrier is
    presumed to be the only one and no identity is fabricated.
S2  RESERVED_METADATA_NAMESPACE  ∩  MODERN_CONTRACT_IDENTITY_NAMESPACE  =  ∅ : a reserved top-level name carrying a
    contract-like value fails closed (invalid_source_contract:INVALID_IDENTITY); valid metadata stays metadata.
S3  ContractRefToken -> {EXPLICIT_CONTRACT_REF | INTERNAL_SOURCE_MARKER | OPAQUE_LEGACY_NON_C2 | INVALID}: the
    boundary was implicit; it is now one executable authority. Runtime behavior is unchanged.

Every witness runs the production path (`contracts_context` / `normalize_domain_contracts` / the shrink ladder),
never a re-implementation. Ablations mutate one production seam, prove the SAME witness goes RED, then restore.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import itertools
import json
import random
from pathlib import Path
from typing import Any

import pytest

from app.agent_review import chunk_payload_builder, payload_cost_model as pcm
from app.agent_review.schemas import ReviewIntake

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate-v1-c2-legacy-differential-oracle.py"

PRESET = "review:deep"
PROJECTION_FIELDS = ("id", "description", "recommended_review_preset")
ANNOTATION_KEYS = ("required", "required_reasons", "effective_contracts")


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


def _ctx(
    profile: dict[str, Any],
    *,
    chunk_contracts: tuple[str, ...] = (),
    selected: str | None = None,
    group: str = "other",
    files: tuple[str, ...] = ("elsewhere/a.txt",),
) -> tuple[dict[str, Any], list[str]]:
    return pcm.contracts_context(
        _intake(profile),
        chunk_files=list(files),
        chunk_contracts=list(chunk_contracts),
        chunk_id="chunk-1",
        selected_contract_pack=selected,
        semantic_group=group,
    )


def _bare(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ANNOTATION_KEYS}


def _packs(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    return [_bare(r) for r in ctx.get("review_packs", [])]


# ---------------------------------------------------------------------------
# S1 -- carrier product domain
# ---------------------------------------------------------------------------

# Each carrier independently: absent / explicit null / blank (cleaned to null) / non-empty.
CARRIER_STATES = ("absent", "null", "blank", "value")
NONEMPTY = {
    "id": ("alpha", "service-x"),
    "description": ("calendar", "service layer"),
    "recommended_review_preset": (PRESET, "a"),
}


def _carrier_pack(states: tuple[str, str, str], pick: int = 0) -> dict[str, Any]:
    pack: dict[str, Any] = {}
    for field, state in zip(PROJECTION_FIELDS, states):
        if state == "null":
            pack[field] = None
        elif state == "blank":
            pack[field] = "  "
        elif state == "value":
            pack[field] = NONEMPTY[field][pick]
    return pack


def _clean(value: Any) -> str | None:
    """Spec of `_clean_text` in the frozen baseline (6bbd2f9): None / blank -> None, else the stripped string."""
    if value is None:
        return None
    return str(value).strip() or None


def _model_row(pack: dict[str, Any]) -> dict[str, Any]:
    """CLOSED-FORM model of the baseline projection: every dict item -> exactly one three-field row."""
    return {field: _clean(pack.get(field)) for field in PROJECTION_FIELDS}


def _model_sorted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["id"] or "", r["description"] or ""))


def _text(row: dict[str, Any]) -> str:
    return ((row["id"] or "") + " " + (row["description"] or "")).lower()


KEYWORDS = {"primary_backend_logic": ("backend", "service", "domain", "api"), "other": ()}


def _model_applicable(
    rows: list[dict[str, Any]], *, include_all: bool, referenced: set[str], selected: str | None, group: str
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        sel = (selected or "").lower()
        by_selection = bool(sel) and (sel in (row["id"] or "").lower() or sel in (row["description"] or "").lower())
        by_relevance = any(k in _text(row) for k in KEYWORDS.get(group, ()))
        if include_all or (row["id"] in referenced) or by_selection or by_relevance:
            out.append(row)
    return out


CARRIER_PRODUCT = list(itertools.product(CARRIER_STATES, repeat=3))  # 4^3 = 64 combinations
ALL_NULL_ROW = {"id": None, "description": None, "recommended_review_preset": None}


@pytest.mark.parametrize("states", CARRIER_PRODUCT, ids=lambda s: "/".join(s))
def test_s1_every_carrier_combination_is_exactly_one_projected_row(states: tuple[str, str, str]) -> None:
    """Totality over the id x description x preset product (each absent/null/blank/value): include_all emits the
    exact baseline row, nulls preserved, never dropped because a particular carrier is missing."""
    pack = _carrier_pack(states)
    ctx, limitations = _ctx({"review_packs": {"packs": [pack]}}, chunk_contracts=("target_profile:review_packs",))
    assert _packs(ctx) == [_model_row(pack)]
    assert not any(lim.startswith("contracts_context_not_relevant:") for lim in limitations)
    assert all(set(r) >= set(PROJECTION_FIELDS) for r in ctx["review_packs"])


def test_s1_the_reported_witnesses() -> None:
    """The two recovered witnesses: preset-only and all-null packs are emitted by the baseline (6bbd2f9)."""
    for pack, expected in (
        ({"recommended_review_preset": PRESET}, {"id": None, "description": None, "recommended_review_preset": PRESET}),
        ({}, ALL_NULL_ROW),
    ):
        ctx, _ = _ctx({"review_packs": {"packs": [pack]}}, chunk_contracts=("target_profile:review_packs",))
        assert _packs(ctx) == [expected]


def test_s1_ordering_and_multiplicity_are_the_baseline_stable_sort() -> None:
    packs = [
        {"recommended_review_preset": "b"},
        {"description": "x", "recommended_review_preset": "2"},
        {"recommended_review_preset": "a"},
        {"description": "x", "recommended_review_preset": "1"},
        {"recommended_review_preset": "b"},
        {"id": "z"},
        {},
    ]
    ctx, _ = _ctx({"review_packs": {"packs": packs}}, chunk_contracts=("target_profile:review_packs",))
    assert _packs(ctx) == _model_sorted([_model_row(p) for p in packs])
    assert [r["recommended_review_preset"] for r in _packs(ctx)[:3]] == ["b", "a", "b"], "ties keep input order"


# --- carrier-domain fuzz: closed-form model (always) + frozen-baseline differential (when git has it) ---------

FUZZ_SEED = 3724
FUZZ_RANDOM_CASES = 4000


def _fuzz_cases() -> list[dict[str, Any]]:
    """Deterministic. The carrier domain is generated, not hand-picked: every pack draws each carrier
    independently from absent/null/blank/value, then is crossed with include_all / selection / explicit ref /
    semantic group / duplicated rows. The exhaustive 64-state product is prepended to the random cases."""
    rng = random.Random(FUZZ_SEED)
    cases: list[dict[str, Any]] = []

    def mode(rng_: random.Random, packs: list[dict[str, Any]]) -> dict[str, Any]:
        chunk_contracts: list[str] = []
        kind = rng_.choice(("include_all", "none", "ref", "include_all"))
        if kind == "include_all":
            chunk_contracts.append("target_profile:review_packs")
        elif kind == "ref":
            ids = [p["id"].strip() for p in packs if isinstance(p.get("id"), str) and p["id"].strip()]
            chunk_contracts.append("contract:" + (rng_.choice(ids) if ids else "nope"))
        selected = None
        if rng_.random() < 0.35:
            pool = [str(v) for p in packs for v in p.values() if isinstance(v, str) and v.strip()] or ["zz"]
            selected = rng_.choice(pool).strip()[: rng_.choice((2, 4, 99))]
        return {
            "packs": packs,
            "chunk_contracts": chunk_contracts,
            "selected": selected or None,
            "group": rng_.choice(("other", "other", "primary_backend_logic")),
        }

    for states in CARRIER_PRODUCT:
        cases.append(
            {"packs": [_carrier_pack(states)], "chunk_contracts": ["target_profile:review_packs"], "selected": None, "group": "other"}
        )
    for _ in range(FUZZ_RANDOM_CASES):
        packs = [
            _carrier_pack(tuple(rng.choice(CARRIER_STATES) for _ in PROJECTION_FIELDS), pick=rng.randrange(2))  # type: ignore[arg-type]
            for _ in range(rng.randint(1, 5))
        ]
        if rng.random() < 0.3:  # duplicated / repeated rows
            packs.append(copy.deepcopy(rng.choice(packs)))
        rng.shuffle(packs)
        cases.append(mode(rng, packs))
    return cases


def _model_expected(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _model_sorted([_model_row(p) for p in case["packs"]])
    referenced = {t.split(":", 1)[1].strip() for t in case["chunk_contracts"] if t.startswith("contract:")}
    return _model_applicable(
        rows,
        include_all="target_profile:review_packs" in case["chunk_contracts"],
        referenced=referenced,
        selected=case["selected"],
        group=case["group"],
    )


def _current_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    ctx, _ = _ctx(
        {"review_packs": {"packs": copy.deepcopy(case["packs"])}},
        chunk_contracts=tuple(case["chunk_contracts"]),
        selected=case["selected"],
        group=case["group"],
    )
    return _packs(ctx)


FUZZ_CASES = _fuzz_cases()


def test_s1_carrier_domain_fuzz_matches_the_closed_form_baseline_model() -> None:
    assert len(FUZZ_CASES) == len(CARRIER_PRODUCT) + FUZZ_RANDOM_CASES  # recorded in the receipt
    divergences = [i for i, case in enumerate(FUZZ_CASES) if _current_rows(case) != _model_expected(case)]
    assert divergences == [], f"{len(divergences)} unexplained divergences, first case: {FUZZ_CASES[divergences[0]] if divergences else ''}"


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("_v1_c2_round4_generator", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_s1_carrier_domain_fuzz_matches_the_frozen_baseline_when_available() -> None:
    """Differential against `git show 6bbd2f9:...` (developer checkout / full-history CI). Where the baseline
    itself raises (None + str on pack relevance) the totalized baseline defines the expectation, with its
    None->"" totalization read back as None -- the same device the oracle generator uses."""
    gen = _load_generator()
    if not gen.baseline_available():
        pytest.skip("frozen baseline commit not available (shallow clone); the closed-form model test still ran")
    source = gen.load_baseline_source()
    baseline = gen._module_from(source, "_v1_c2_round4_baseline")
    totalized = gen._module_from(gen._totalized(source), "_v1_c2_round4_baseline_totalized")

    def expected(case: dict[str, Any]) -> list[dict[str, Any]]:
        case_input = {
            "target_profile": {"review_packs": {"packs": copy.deepcopy(case["packs"])}},
            "chunk_files": ["elsewhere/a.txt"],
            "chunk_contracts": list(case["chunk_contracts"]),
            "selected_contract_pack": case["selected"],
            "semantic_group": case["group"],
            "chunk_id": "chunk-1",
        }
        observed = gen.run_case(baseline, case_input)
        if observed["outcome"] == "raises":
            observed = gen.run_case(totalized, case_input)
            return [{k: (v or None) for k, v in row.items()} for row in observed["packs"]]
        return observed["packs"]

    divergences = [i for i, case in enumerate(FUZZ_CASES) if _current_rows(case) != expected(case)]
    assert divergences == [], f"{len(divergences)} unexplained divergences vs frozen baseline"


# --- S1 required floor + loss label --------------------------------------------------------------------------

IDLESS_STATES = [s for s in CARRIER_PRODUCT if s[0] in ("absent", "null", "blank")]


@pytest.mark.parametrize("states", IDLESS_STATES, ids=lambda s: "/".join(s))
def test_s1_idless_required_floor_is_the_exact_three_field_projection(states: tuple[str, str, str]) -> None:
    pack = _carrier_pack(states)
    ctx, _ = _ctx({"review_packs": {"packs": [pack]}}, chunk_contracts=("target_profile:review_packs",))
    row = ctx["review_packs"][0]
    assert row["required"] is True and row["id"] is None, "no identity is fabricated"
    projection = _model_row(pack)
    assert pcm.required_pack_floor_keys(row) == frozenset({*PROJECTION_FIELDS, "effective_contracts", "required"})
    assert pcm.minimal_contracts_context(ctx)["review_packs"] == [{**projection, "required": True}]
    cleaned = pcm.clean_contracts_context_for_payload(ctx)
    assert cleaned["review_packs"] == [{**projection, "required": True}]
    snapshots, limitations = _shrink(cleaned)
    assert all(s["review_packs"] in ([{**projection, "required": True}], []) for s in snapshots), "no carrier stripped"
    assert limitations == [f"required_contract_pack_context_lost:{pcm.unidentified_pack_loss_label(projection)}"]


def test_s1_identified_pack_floor_is_unchanged() -> None:
    ctx, _ = _ctx({"review_packs": {"packs": [{"id": "alpha", "recommended_review_preset": PRESET}]}}, chunk_contracts=("target_profile:review_packs",))
    assert pcm.minimal_contracts_context(ctx)["review_packs"] == [{"id": "alpha", "required": True}]
    assert pcm.required_pack_floor_keys(ctx["review_packs"][0]) == frozenset({"id", "effective_contracts", "required"})


def _shrink(ctx: dict[str, Any], limitations: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    payload: dict[str, Any] = {"chunk_context": {"contracts_context": ctx}, "limitations": list(limitations or [])}
    snapshots: list[dict[str, Any]] = []
    for _ in range(40):
        if not chunk_payload_builder._shrink_contracts_context(payload):
            break
        snapshots.append(copy.deepcopy(payload["chunk_context"]["contracts_context"]))
    return snapshots, payload["limitations"]


def _distinct_idless_ctx() -> dict[str, Any]:
    """Two required id-less packs whose DESCRIPTIONS are both null and which differ only in another carrier."""
    ctx, _ = _ctx(
        {"review_packs": {"packs": [{"recommended_review_preset": "a"}, {"recommended_review_preset": "b"}, {}]}},
        chunk_contracts=("target_profile:review_packs",),
    )
    return pcm.clean_contracts_context_for_payload(ctx)


def _loss_witness() -> int:
    _, limitations = _shrink(_distinct_idless_ctx())
    return len(limitations)


def test_s1_loss_label_discriminates_idless_packs_without_fabricating_identity() -> None:
    labels = {pcm.unidentified_pack_loss_label(p) for p in ({"recommended_review_preset": "a"}, {"recommended_review_preset": "b"}, {})}
    assert len(labels) == 3, "null descriptions must not collapse distinct lost packs"
    assert pcm.unidentified_pack_loss_label({"recommended_review_preset": "a"}) == pcm.unidentified_pack_loss_label(
        {"id": None, "description": None, "recommended_review_preset": "a", "required": True}
    ), "the label is a function of the observable projection only"
    for label in labels:
        assert label.startswith("unidentified_legacy_pack:") and len(label.split(":", 1)[1]) == 12
    expected = hashlib.sha256(pcm.canonical_json(dict(zip(PROJECTION_FIELDS, (None, None, "a")))).encode()).hexdigest()[:12]
    assert pcm.unidentified_pack_loss_label({"recommended_review_preset": "a"}) == f"unidentified_legacy_pack:{expected}"
    assert _loss_witness() == 3, "three distinct lost packs -> three distinct typed limitations"


# ---------------------------------------------------------------------------
# S1 ablations
# ---------------------------------------------------------------------------


def _preset_only_rows() -> int:
    ctx, _ = _ctx({"review_packs": {"packs": [{"recommended_review_preset": PRESET}]}}, chunk_contracts=("target_profile:review_packs",))
    return len(ctx["review_packs"])


def test_ab_r4_s1_legacy_admission_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R4-S1-LEGACY-ADMISSION-FILTER: restore `bool(id or description)` row admission."""

    def filtered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = [pcm._legacy_pack_row(i) for i in items if isinstance(i, dict)]
        rows = [r for r in rows if r.get("id") or r.get("description")]
        return sorted(rows, key=lambda r: (r.get("id") or "", r.get("description") or ""))

    assert _preset_only_rows() == 1
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_pack_rows", filtered)
        assert _preset_only_rows() == 0, "mutant must go RED"
    assert _preset_only_rows() == 1


def test_ab_r4_s1_idless_floor_description_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R4-S1-IDLESS-FLOOR-DESCRIPTION-ONLY: the predecessor floor (description is the only carrier)."""
    pack = {"recommended_review_preset": PRESET}

    def witness() -> bool:
        ctx, _ = _ctx({"review_packs": {"packs": [pack]}}, chunk_contracts=("target_profile:review_packs",))
        floor = {**_model_row(pack), "required": True}
        snapshots, _ = _shrink(pcm.clean_contracts_context_for_payload(ctx))
        return pcm.minimal_contracts_context(ctx)["review_packs"] == [floor] and all(
            s["review_packs"] in ([floor], []) for s in snapshots
        )

    def old_floor(p: dict[str, Any]) -> frozenset[str]:
        return frozenset({"description", "effective_contracts", "required"} if not p.get("id") else {"id", "effective_contracts", "required"})

    def old_minimal(p: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if p.get("id"):
            out["id"] = p["id"]
        elif p.get("description"):
            out["description"] = p["description"]
        if p.get("required") is True:
            out["required"] = True
        return out

    assert witness() is True
    with monkeypatch.context() as m:
        m.setattr(pcm, "required_pack_floor_keys", old_floor)
        m.setattr(pcm, "minimal_pack_context", old_minimal)
        assert witness() is False, "mutant must go RED"
    assert witness() is True


def test_ab_r4_s1_loss_label_description_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R4-S1-LOSS-LABEL-DESCRIPTION-ONLY: the predecessor label digests only the description."""

    def old_label(pack: dict[str, Any]) -> str:
        digest = hashlib.sha256(str(pack.get("description") or "").encode("utf-8")).hexdigest()[:12]
        return f"{pcm.UNIDENTIFIED_LEGACY_PACK_LOSS_LABEL}:{digest}"

    assert _loss_witness() == 3
    with monkeypatch.context() as m:
        m.setattr(pcm, "unidentified_pack_loss_label", old_label)
        assert _loss_witness() == 1, "mutant must go RED: three lost packs collapse into one limitation"
    assert _loss_witness() == 3


# ---------------------------------------------------------------------------
# S2 -- reserved metadata namespace vs modern contract identity namespace
# ---------------------------------------------------------------------------

RESERVED = ("version", "schema_version", "updated", "system", "metadata")

VALID_METADATA_VALUES = {
    "scalar_str": "1",
    "scalar_int": 2,
    "scalar_bool": True,
    "mapping_of_scalars": {"name": "AgentEscala", "owner": "team"},
    "mapping_nested_dict": {"build": {"number": 7}},
    "mapping_list_of_numbers": {"revisions": [1, 2, 3]},
}
COLLISION_VALUES = {
    "rules_list": {"rules": ["rule"]},
    "slot_rules": {"slot_rules": [{"rule": "r"}]},
    "paths": {"paths": ["backend/**"]},
    "patterns": {"patterns": ["backend/*"]},
    "path": {"path": "backend/a.py"},
    "file_path": {"file_path": "backend/a.py"},
    "scope": {"scope": "global"},
    "is_global": {"is_global": True},
    "description": {"description": "d"},
    "id": {"id": "x"},
    "named_string_section": {"tags": ["a", "b"]},
    "top_level_string_list": ["contract-style top-level string-list"],
    "top_level_rule_dicts": [{"rule": "r"}],
    "top_level_empty_list": [],
}
MALFORMED_METADATA_VALUES = {
    "null": None,
    "list_of_numbers": [1, 2],
    "mixed_list": ["a", {"rule": "r"}],
    "mapping_with_non_scalar_leaf": {"x": None},
    "mapping_with_blank_key": {" ": "v"},
}


def _normalize(document: Any) -> tuple[list[dict[str, Any]], str, str | None, list[str]]:
    return pcm.normalize_domain_contracts(document)


def test_s2_reserved_namespace_is_exactly_the_five_names_and_disjoint_from_identities() -> None:
    assert pcm.RESERVED_DOMAIN_CONTRACT_METADATA_KEYS == frozenset(RESERVED)
    for key in RESERVED:
        rows, state, _, limitations = _normalize({key: {"rules": ["r"]}})
        assert rows == [] and state == pcm.SOURCE_STATE_INVALID, f"{key} must never become a contract identity"
        assert limitations == ["invalid_source_contract:INVALID_IDENTITY"]


@pytest.mark.parametrize("key", RESERVED)
@pytest.mark.parametrize("name", list(VALID_METADATA_VALUES))
def test_s2_valid_metadata_positive_for_every_reserved_name(key: str, name: str) -> None:
    rows, state, subtype, limitations = _normalize({key: VALID_METADATA_VALUES[name]})
    assert (rows, state, subtype, limitations) == ([], pcm.SOURCE_STATE_PRESENT_VALID, None, []), "valid metadata yields no contract row"
    rows, state, _, limitations = _normalize({key: VALID_METADATA_VALUES[name], "real-contract": ["rule one"]})
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and [r["id"] for r in rows] == ["real-contract"] and limitations == []


@pytest.mark.parametrize("key", RESERVED)
@pytest.mark.parametrize("name", list(COLLISION_VALUES))
def test_s2_contract_like_value_under_every_reserved_name_fails_closed(key: str, name: str) -> None:
    for document in ({key: COLLISION_VALUES[name]}, {" " + key + " ": COLLISION_VALUES[name]}, {"real-contract": ["rule"], key: COLLISION_VALUES[name]}):
        rows, state, subtype, limitations = _normalize(copy.deepcopy(document))
        assert rows == [], "a collision never yields a partial source"
        assert state == pcm.SOURCE_STATE_INVALID and subtype == pcm.SUBTYPE_INVALID_IDENTITY
        assert limitations == ["invalid_source_contract:INVALID_IDENTITY"]


@pytest.mark.parametrize("key", RESERVED)
@pytest.mark.parametrize("name", list(MALFORMED_METADATA_VALUES))
def test_s2_malformed_metadata_keeps_its_own_unsupported_class(key: str, name: str) -> None:
    """ReservedMetadataMalformed != ReservedIdentityCollision: a non-contract-like invalid value stays UNSUPPORTED."""
    rows, state, subtype, limitations = _normalize({key: MALFORMED_METADATA_VALUES[name]})
    assert rows == [] and state == pcm.SOURCE_STATE_INVALID and subtype == pcm.SUBTYPE_UNSUPPORTED_NONEMPTY
    assert limitations == ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]


def test_s2_the_same_shape_under_an_unreserved_name_is_still_a_contract() -> None:
    for name, value in (("system-rules", {"rules": ["rule"]}), ("versions", {"paths": ["backend/**"]}), ("updated-by", ["a"])):
        rows, state, _, _ = _normalize({name: copy.deepcopy(value)})
        assert state == pcm.SOURCE_STATE_PRESENT_VALID and [r["id"] for r in rows] == [name]


def test_s2_collision_surfaces_through_the_production_context_path() -> None:
    for key in RESERVED:
        _, limitations = _ctx({"domain_contracts": {key: {"rules": ["rule"]}}}, chunk_contracts=("target_profile:domain_contracts",))
        assert "invalid_source_contract:INVALID_IDENTITY" in limitations, key


def test_ab_r4_s2_reserved_namespace_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R4-S2-RESERVED-NAMESPACE-COLLISION: the predecessor `if key in RESERVED: validate_metadata(); continue`."""

    def witness() -> str:
        return _normalize({"system": {"rules": ["rule"]}})[1]

    assert witness() == pcm.SOURCE_STATE_INVALID
    with monkeypatch.context() as m:
        m.setattr(pcm, "reserved_value_is_contract_like", lambda value: False)
        assert witness() == pcm.SOURCE_STATE_PRESENT_VALID, "mutant must go RED (silently metadata)"
        assert _normalize({"version": {"paths": ["backend/**"]}})[1] == pcm.SOURCE_STATE_PRESENT_VALID
    assert witness() == pcm.SOURCE_STATE_INVALID


# ---------------------------------------------------------------------------
# S3 -- contract reference token boundary (declared, not changed)
# ---------------------------------------------------------------------------

CONTRACT_FOO = {"domain_contracts": {"rules": [{"id": "foo", "description": "the foo rule"}]}}


def test_s3_token_classes_are_one_total_authority() -> None:
    cls = pcm.classify_contract_ref_token
    assert cls("foo").token_class == pcm.TOKEN_CLASS_OPAQUE_LEGACY_NON_C2
    explicit = cls("contract:foo")
    assert (explicit.token_class, explicit.identity) == (pcm.TOKEN_CLASS_EXPLICIT_CONTRACT_REF, "foo")
    assert cls("contract: spaced ").identity == "spaced", "the explicit identity is trimmed"
    assert cls("contract:x:y").identity == "x:y", "everything after the FIRST colon"
    for marker in ("target_profile:domain_contracts", "target_profile:review_packs"):
        assert (cls(marker).token_class, cls(marker).marker, cls(marker).identity) == (pcm.TOKEN_CLASS_INTERNAL_SOURCE_MARKER, marker, None)
    assert cls("contract:").token_class == pcm.TOKEN_CLASS_INVALID
    assert cls("contract:").limitation == "unresolved_contract_reference:EMPTY_IDENTITY"
    assert cls("contract:   ").token_class == pcm.TOKEN_CLASS_INVALID
    assert cls("").token_class == pcm.TOKEN_CLASS_INVALID and cls("   ").limitation is None, "blank stays inert (producer reports it)"
    assert cls(5).token_class == pcm.TOKEN_CLASS_INVALID and cls(None).limitation == "unresolved_contract_reference:MALFORMED_MEMBER"
    for near in ("Contract:foo", "contracts:foo", " contract:foo", "TARGET_PROFILE:DOMAIN_CONTRACTS", " target_profile:review_packs", "target_profile:other"):
        assert cls(near).token_class == pcm.TOKEN_CLASS_OPAQUE_LEGACY_NON_C2, near
    assert {cls(t).token_class for t in ("foo", "contract:foo", "contract:", "target_profile:review_packs")} == pcm.TOKEN_CLASSES


def test_s3_bare_token_is_inert_and_explicit_token_is_the_only_c2_relation() -> None:
    bare, bare_limits = _ctx(CONTRACT_FOO, chunk_contracts=("foo",))
    assert bare["domain_contracts"] == [] and "contracts_context_not_relevant:chunk-1" in bare_limits
    assert not any(lim.startswith(("unresolved_contract_reference", "required_source_absent")) for lim in bare_limits)
    explicit, explicit_limits = _ctx(CONTRACT_FOO, chunk_contracts=("contract:foo",))
    assert [c["id"] for c in explicit["domain_contracts"]] == ["foo"] and explicit_limits == []
    # an opaque token equal to the contract's id/description/path-ish text never establishes a relation
    for opaque in ("foo", "the foo rule", "contracts:foo", "Contract:foo", "target_profile:other"):
        ctx, limits = _ctx(CONTRACT_FOO, chunk_contracts=(opaque,))
        assert ctx["domain_contracts"] == [] and not any(lim.startswith("unresolved_contract_reference") for lim in limits), opaque
    _, empty_limits = _ctx(CONTRACT_FOO, chunk_contracts=("contract:",))
    assert "unresolved_contract_reference:EMPTY_IDENTITY" in empty_limits
    for marker, key in (("target_profile:domain_contracts", "domain_contracts"), ("target_profile:review_packs", "review_packs")):
        profile = {**CONTRACT_FOO, "review_packs": {"packs": [{"id": "p1"}]}}
        ctx, _ = _ctx(profile, chunk_contracts=(marker,))
        assert ctx[key], marker
        assert ctx["review_packs" if key == "domain_contracts" else "domain_contracts"] == [] or key == "review_packs"


def test_s3_producer_emits_only_classified_tokens_and_the_planner_representation_is_unchanged() -> None:
    profile = {**CONTRACT_FOO, "review_packs": {"packs": [{"id": "p1"}]}, "contracts": ["foo", "contract:foo"]}
    refs, limitations = pcm.parse_contract_refs(_intake(profile))
    assert refs == ["target_profile:domain_contracts", "target_profile:review_packs", "foo", "contract:foo"] and limitations == []
    assert {pcm.classify_contract_ref_token(r).token_class for r in refs} == {
        pcm.TOKEN_CLASS_INTERNAL_SOURCE_MARKER,
        pcm.TOKEN_CLASS_OPAQUE_LEGACY_NON_C2,
        pcm.TOKEN_CLASS_EXPLICIT_CONTRACT_REF,
    }


def test_s3_only_the_classifier_tests_token_grammar_inside_contracts_context() -> None:
    source = (REPO_ROOT / "app" / "agent_review" / "payload_cost_model.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "contracts_context")
    literals = {n.value for n in ast.walk(func) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not {v for v in literals if v.startswith(("contract:", "target_profile:"))}, "ad hoc token grammar outside the authority"
    assert not any(isinstance(n, ast.Attribute) and n.attr in {"startswith", "split"} and isinstance(n.value, ast.Name) and n.value.id == "item" for n in ast.walk(func))


def test_s3_non_string_member_is_a_typed_limitation_not_a_crash() -> None:
    """The one direct-call-only delta: a non-string member used to raise AttributeError. The producer filters it
    upstream (parse_contract_refs), so no production path reaches this; every STRING behaves as before."""
    ctx, limits = pcm.contracts_context(
        _intake(CONTRACT_FOO), chunk_files=["elsewhere/a.txt"], chunk_contracts=[5], chunk_id="chunk-1", selected_contract_pack=None, semantic_group="other"
    )
    assert "unresolved_contract_reference:MALFORMED_MEMBER" in limits and ctx["domain_contracts"] == []


def test_s3_string_token_behavior_equals_the_frozen_baseline_when_available() -> None:
    """Opaque strings, source markers and RESOLVED explicit references observe exactly as the baseline. An explicit
    reference that resolves nothing is the declared Gate A fail-closed divergence (cycle 1, 4178603200)."""
    gen = _load_generator()
    if not gen.baseline_available():
        pytest.skip("frozen baseline commit not available (shallow clone)")
    baseline = gen._module_from(gen.load_baseline_source(), "_v1_c2_round4_token_baseline")
    profile = {**CONTRACT_FOO, "review_packs": {"packs": [{"id": "p1", "description": "pack one"}]}}
    for token in ("foo", "p1", "the foo rule", "Contract:foo", "contracts:foo", "TARGET_PROFILE:DOMAIN_CONTRACTS", " target_profile:review_packs",
                  "target_profile:other", "contract:foo", "contract:p1", "target_profile:domain_contracts", "target_profile:review_packs"):
        case_input = {"target_profile": profile, "chunk_files": ["elsewhere/a.txt"], "chunk_contracts": [token], "selected_contract_pack": None,
                      "semantic_group": "other", "chunk_id": "chunk-1"}
        ctx, limits = _ctx(profile, chunk_contracts=(token,))
        assert {"outcome": "ok", **gen.observe(ctx, limits)} == gen.run_case(baseline, case_input), token


def test_ab_r4_s3_bare_token_becomes_a_relation(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R4-S3-BARE-TOKEN-RELATION: a classifier that treats every opaque string as an explicit reference."""
    real = pcm.classify_contract_ref_token

    def promoting(token: Any) -> Any:
        result = real(token)
        if result.token_class == pcm.TOKEN_CLASS_OPAQUE_LEGACY_NON_C2:
            return pcm.ContractRefToken(pcm.TOKEN_CLASS_EXPLICIT_CONTRACT_REF, identity=token.strip())
        return result

    def witness() -> list[str]:
        return [c["id"] for c in _ctx(CONTRACT_FOO, chunk_contracts=("foo",))[0]["domain_contracts"]]

    assert witness() == []
    with monkeypatch.context() as m:
        m.setattr(pcm, "classify_contract_ref_token", promoting)
        assert witness() == ["foo"], "mutant must go RED"
    assert witness() == []
