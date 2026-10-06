"""Durable differential oracle: current LEGACY semantics == frozen baseline 6bbd2f9.

The fixture (`fixtures/v1_c2_legacy_differential_observations.json`) holds INPUT +
EXPECTED OBSERVATION derived from the frozen baseline by
`scripts/generate-v1-c2-legacy-differential-oracle.py`. This module never reads
git history in CI (the workflow checks out shallowly): it consumes the versioned
fixture, verifies its provenance (commit + blob oid), proves every baseline
predicate has a discriminating case (MC/DC-style kill map), and compares the
current engine against every observation. When the baseline commit IS available
(developer checkout) it additionally proves the fixture is exactly what a fresh
generation produces.

Mode separation (Mode -> ... -> ObservableResult): a LEGACY source's observation is
decided only by the baseline's predicates; modern semantics must never leak into it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.agent_review import payload_cost_model as pcm
from app.agent_review.schemas import ReviewIntake

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "v1_c2_legacy_differential_observations.json"
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate-v1-c2-legacy-differential-oracle.py"

# Pinned identity of the frozen legacy differential authority. Deliberately duplicated here:
# editing the fixture alone must not be able to re-point the oracle.
PINNED_BASELINE_COMMIT = "6bbd2f949989da3e90e1d9c527e37059c0b628ff"
PINNED_BASELINE_BLOB_OID = "41423394e69b4fa67785ac01088757bdff917001"
PINNED_SCHEMA = "agent-review.v1-c2-legacy-differential-observations.v1"

REQUIRED_COUNTERMODELS = {
    "CM-C4-LEGACY-INCLUDE-ALL-CONTRACT",
    "CM-C4-LEGACY-PACK-INCLUDE-ALL",
    "CM-C4-LEGACY-PACK-EXPLICIT-ID-REF",
    "CM-C4-LEGACY-PACK-RELEVANCE",
    "CM-C4-LEGACY-CONTRACT-RELEVANCE",
    "CM-C4-DESCRIPTION-ONLY-PACK-PRESERVED",
    "CM-C4-DESCRIPTION-ONLY-PACK-SELECTED",
    "CM-C4-LEGACY-NOT-RELEVANT",
    "CM-C4-LEGACY-PACK-PROJECTION",
    "CM-C4-LEGACY-UNKNOWN-FIELDS-IGNORED",
    "CM-C4-LEGACY-CONTRACT-ROWS-ADMITTED",
    "CM-C4-LEGACY-PACK-ROWS-EXACT",
    "CM-C4-LEGACY-PACK-CARRIER-PRODUCT",
}

# Gate A fail-closed typed limitations that INTENTIONALLY change the observation relative to the
# baseline: an unresolved explicit reference / missing selected pack is never "not relevant".
# authority: cycle-1 findings 4178603200 / 4178603183, AGENTS.md:37-39 (fail-closed for identity).
# An explicit `contract:<id>` is a DECLARED contract identity reference, so with no domain_contracts source
# it also reports required_source_absent (cycle-3 4179993600): SourceAbsent != RelationUnresolved.
INTENTIONAL_DIVERGENCES: dict[str, dict[str, Any]] = {
    "contract.explicit_ref.miss": {"limitations": ["unresolved_contract_reference:nope"]},
    "pack.explicit_ref.miss": {
        "limitations": ["unresolved_contract_reference:nope", "required_source_absent:domain_contracts"]
    },
    "pack.selected.miss": {"limitations": ["selected_contract_pack_missing:zzz"]},
    "contract.explicit_ref.case_differs.miss": {"limitations": ["unresolved_contract_reference:ZZ-RULE"]},
    "contract.explicit_ref.description_equal.miss": {"limitations": ["unresolved_contract_reference:zzz"]},
    "contract.explicit_ref.substring.miss": {"limitations": ["unresolved_contract_reference:zz"]},
    "pack.explicit_ref.case_differs.miss": {
        "limitations": ["unresolved_contract_reference:ALPHA", "required_source_absent:domain_contracts"]
    },
    "pack.explicit_ref.description_equal.miss": {
        "limitations": ["unresolved_contract_reference:Alpha pack", "required_source_absent:domain_contracts"]
    },
    "pack.explicit_ref.substring.miss": {
        "limitations": ["unresolved_contract_reference:alph", "required_source_absent:domain_contracts"]
    },
    "pack.selected.reverse_alias.miss": {"limitations": ["selected_contract_pack_missing:foo-alpha"]},
    # Round-4 S1: the preset is a projected carrier but never a selection carrier (id/description only)
    "pack.preset_only.selected_by_preset.miss": {"limitations": ["selected_contract_pack_missing:review:deep"]},
    # an empty explicit token `contract:` is a typed EMPTY_IDENTITY limitation, not the baseline's silent inert token
    "pack.relevance.baseline_raises.idless_x_empty_explicit_token": {"limitations": ["unresolved_contract_reference:EMPTY_IDENTITY"]},
}


def _load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = _load_fixture()
CASES: list[dict[str, Any]] = FIXTURE["cases"]


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


ANNOTATION_KEYS = ("required", "required_reasons", "effective_contracts")


def _observe(ctx: dict[str, Any], limitations: list[str]) -> dict[str, Any]:
    def row(value: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in value.items() if k not in ANNOTATION_KEYS}

    return {
        "contracts": [row(r) for r in ctx.get("domain_contracts", [])],
        "packs": [row(r) for r in ctx.get("review_packs", [])],
        "not_relevant": any(lim.startswith("contracts_context_not_relevant:") for lim in limitations),
    }


def _run_current(case_input: dict[str, Any], *, profile: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    return pcm.contracts_context(
        _intake(profile if profile is not None else case_input["target_profile"]),
        chunk_files=list(case_input["chunk_files"]),
        chunk_contracts=list(case_input["chunk_contracts"]),
        chunk_id=case_input["chunk_id"],
        selected_contract_pack=case_input["selected_contract_pack"],
        semantic_group=case_input["semantic_group"],
    )


def _expected(case: dict[str, Any]) -> dict[str, Any]:
    baseline = case["baseline_observation"]
    if baseline["outcome"] == "raises":
        baseline = case["totalized_baseline_observation"]
    return {k: baseline[k] for k in ("contracts", "packs", "not_relevant")}


def _expected_current(case: dict[str, Any]) -> dict[str, Any]:
    expected = _expected(case)
    if case["id"] in INTENTIONAL_DIVERGENCES:
        # typed fail-closed limitation suppresses the baseline's not_relevant consequence
        expected = {**expected, "not_relevant": False}
    return expected


def run_oracle() -> list[str]:
    """Every case whose current observation differs from the baseline-derived expectation."""
    mismatches: list[str] = []
    for case in CASES:
        ctx, limits = _run_current(case["input"])
        if _observe(ctx, limits) != _expected_current(case):
            mismatches.append(case["id"])
    return mismatches


# ---------------------------------------------------------------------------
# Provenance and completeness of the oracle itself
# ---------------------------------------------------------------------------


def test_oracle_declares_required_provenance_fields() -> None:
    for field in (
        "schema",
        "baseline_commit",
        "baseline_payload_cost_model_blob_oid",
        "generation_method",
        "observable_projection",
        "cases",
    ):
        assert field in FIXTURE, field
    assert FIXTURE["schema"] == PINNED_SCHEMA
    assert FIXTURE["baseline_commit"] == PINNED_BASELINE_COMMIT
    assert FIXTURE["baseline_payload_cost_model_blob_oid"] == PINNED_BASELINE_BLOB_OID
    assert re.fullmatch(r"[0-9a-f]{40}", FIXTURE["baseline_commit"])
    assert FIXTURE["admitted_baseline_shapes"] and FIXTURE["additive_compatibility_shapes"]


def test_oracle_stores_inputs_and_observations_not_an_algorithm() -> None:
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    assert "def " not in text and "lambda" not in text
    for case in CASES:
        assert set(case) >= {"id", "covers", "input", "baseline_observation"}


def test_oracle_case_ids_are_unique_and_countermodels_present() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert REQUIRED_COUNTERMODELS <= {c["countermodel"] for c in CASES if c["countermodel"]}


def test_every_live_baseline_predicate_has_a_discriminating_case() -> None:
    """MC/DC-style accounting: the kill map names, per predicate, the cases whose baseline observation changes
    when that predicate alone is mutated. Live predicates need >= 1 killer; the only survivors are the
    declared redundant (subsumed) and dead (structurally unreachable) predicates."""
    kill_map = FIXTURE["predicate_kill_map"]
    case_ids = {c["id"] for c in CASES}
    covers_by_case = {c["id"]: set(c["covers"]) for c in CASES}
    kinds = {p["id"]: p["kind"] for p in FIXTURE["baseline_predicates"]}
    assert set(kill_map) == set(kinds)
    for predicate, killers in kill_map.items():
        assert set(killers) <= case_ids, predicate
        if kinds[predicate] == "live":
            assert killers, f"STOP_LEGACY_DIFFERENTIAL_BRANCH_GAP: {predicate}"
            assert any(predicate in covers_by_case[k] for k in killers), f"no killer declares {predicate}: {predicate}"
        else:
            assert killers == [], predicate
    survivors = {p for p, k in kinds.items() if k != "live"}
    assert survivors == {"P.sel.id_eq", "P.sel.desc_eq", "P.match_chunk", "D.exact_backslash"}


def test_corpus_covers_the_requested_branch_matrix() -> None:
    covers = {tag for c in CASES for tag in c["covers"]}
    for tag in (
        "C.include_all",
        "C.explicit_ref",
        "C.match.paths",
        "C.match.patterns",
        "C.match.global.scope",
        "C.match.global.flag",
        "C.relevance",
        "P.include_all",
        "P.explicit_ref",
        "P.selected.guard",
        "P.sel.id_sub",
        "P.sel.desc_sub",
        "P.relevance",
        "P.match_chunk",
        "TERM.not_relevant",
    ):
        assert tag in covers, tag
    ids = {c["id"] for c in CASES}
    # hit/miss pairs requested by the task
    for hit, miss in [
        ("contract.include_all.true", "contract.include_all.false"),
        ("contract.explicit_ref.hit", "contract.explicit_ref.miss"),
        ("contract.paths.exact.hit", "contract.paths.substring.miss"),
        ("contract.patterns.substring.hit", "contract.patterns.miss"),
        ("contract.global.scope.lower", "contract.global.nonglobal_scope.miss"),
        ("pack.include_all.true", "pack.include_all.false"),
        ("pack.explicit_ref.hit", "pack.explicit_ref.miss"),
        ("pack.selected.exact_id", "pack.selected.miss"),
        ("pack.selected.fuzzy_id", "pack.selected.miss"),
        ("pack.selected.exact_description", "pack.selected.miss"),
        ("pack.selected.fuzzy_description", "pack.selected.miss"),
        ("pack.relevance.id_part", "pack.relevance.miss"),
        ("terminal.contract_only", "terminal.zero_context.not_relevant"),
    ]:
        assert hit in ids and miss in ids, (hit, miss)
    # id-only / description-only / id+description, for contracts and for packs
    for required in (
        "contract.relevance.id_only",
        "contract.relevance.description_only",
        "contract.relevance.id_and_description",
        "contract.carriers.include_all",
        "pack.description_only.selected",
        "pack.description_only.preserved",
        "pack.id_only.include_all",
        "pack.relevance.id_part",
        "pack.relevance.desc_part",
    ):
        assert required in ids, required


def test_baseline_defects_are_declared_not_hidden() -> None:
    """Where the baseline itself raises (None + str on a pack lacking id or description), the oracle records the
    raise AND the totalized expectation the current engine must meet."""
    raising = [c for c in CASES if c["baseline_observation"]["outcome"] == "raises"]
    assert {c["id"] for c in raising} == {
        "pack.relevance.baseline_raises.id_only",
        "pack.relevance.baseline_raises.description_only",
        "pack.relevance.baseline_raises.preset_only",
        "pack.relevance.baseline_raises.all_null",
        "pack.relevance.baseline_raises.idless_x_empty_explicit_token",
        "pack.relevance.baseline_raises.sibling_row_values",
    }
    for case in raising:
        assert case["baseline_observation"]["error"] == "TypeError"
        assert "totalized_baseline_observation" in case


# ---------------------------------------------------------------------------
# Current engine vs baseline-derived observations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_current_legacy_observation_equals_frozen_baseline(case: dict[str, Any]) -> None:
    ctx, limits = _run_current(case["input"])
    assert _observe(ctx, limits) == _expected_current(case)

    allowed = {f"contracts_context_not_relevant:{case['input']['chunk_id']}"}
    divergence = INTENTIONAL_DIVERGENCES.get(case["id"])
    if divergence:
        for limitation in divergence["limitations"]:
            assert limitation in limits, "intentional divergence must be the typed fail-closed limitation"
            allowed.add(limitation)
    unexpected = [lim for lim in limits if lim not in allowed]
    assert unexpected == [], f"legacy input produced a Gate-A limitation the baseline never had: {unexpected}"


def test_oracle_is_green_on_the_current_engine() -> None:
    assert run_oracle() == []


def _wrap_additive(profile: dict[str, Any]) -> dict[str, Any]:
    wrapped = copy.deepcopy(profile)
    if "rules" in wrapped.get("domain_contracts", {}):
        wrapped["domain_contracts"] = wrapped["domain_contracts"]["rules"]
    if "packs" in wrapped.get("review_packs", {}) and isinstance(wrapped["review_packs"]["packs"], list):
        wrapped["review_packs"] = wrapped["review_packs"]["packs"]
    return wrapped


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c["input"]["target_profile"]],
    ids=[c["id"] for c in CASES if c["input"]["target_profile"]],
)
def test_additive_compatibility_shapes_do_not_alter_baseline_domain_observations(case: dict[str, Any]) -> None:
    """ADDITIVE_COMPATIBILITY: top-level lists are outside the baseline domain; they must behave exactly like
    the equivalent `{"rules"|"packs": [...]}` envelope, never differently."""
    envelope = _observe(*_run_current(case["input"]))
    additive = _observe(*_run_current(case["input"], profile=_wrap_additive(case["input"]["target_profile"])))
    assert additive == envelope


# ---------------------------------------------------------------------------
# Regeneration (developer checkouts only) and oracle ablation
# ---------------------------------------------------------------------------


def _baseline_in_history() -> bool:
    try:
        out = subprocess.run(
            ["git", "cat-file", "-t", PINNED_BASELINE_COMMIT], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return out.stdout.strip() == "commit"


@pytest.mark.skipif(not _baseline_in_history(), reason="baseline commit not in history (shallow CI checkout); CI consumes the versioned fixture")
def test_fixture_equals_a_fresh_generation_from_the_frozen_baseline() -> None:
    spec = importlib.util.spec_from_file_location("_gen_v1_c2_oracle", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.BASELINE_COMMIT == PINNED_BASELINE_COMMIT
    assert module.BASELINE_BLOB_OID == PINNED_BASELINE_BLOB_OID
    assert module.render(module.generate()) == FIXTURE_PATH.read_text(encoding="utf-8")
    blob = subprocess.run(
        ["git", "rev-parse", f"{PINNED_BASELINE_COMMIT}:app/agent_review/payload_cost_model.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert blob == PINNED_BASELINE_BLOB_OID


def test_ab_c4_legacy_differential_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-C4-LEGACY-DIFFERENTIAL-ORACLE: each family of modern-semantics leak into legacy is caught by the
    oracle. Same oracle GREEN -> leak injected into ONE production seam -> RED -> restore -> GREEN."""
    assert run_oracle() == []

    # leak 1: legacy `paths` regain substring/glob recovery (the cycle-2 `_contract_matches_chunk` leak)
    original_match = pcm._contract_matches_chunk

    def leaking_match(contract: dict[str, Any], *, chunk_files: set[str], format: str = pcm.FORMAT_MODERN_MAPPING) -> bool:
        if original_match(contract, chunk_files=chunk_files, format=format):
            return True
        return format == pcm.FORMAT_LEGACY_FLAT and any(
            pcm._matches_legacy_pattern(path, list(pcm._paths_from_item(contract))) for path in chunk_files
        )

    with monkeypatch.context() as m:
        m.setattr(pcm, "_contract_matches_chunk", leaking_match)
        assert run_oracle(), "legacy paths substring leak must be caught"
    assert run_oracle() == []

    # leak 2: the legacy pack projection acquires modern fields (paths/patterns/is_global)
    original_row = pcm._legacy_pack_row

    def leaking_row(item: dict[str, Any]) -> dict[str, Any]:
        row = original_row(item)
        row.update(
            pcm._drop_empty_contract_fields(
                {
                    "paths": pcm._sanitize_contract_paths(item.get("paths")),
                    "is_global": item.get("is_global") is True or pcm.classify_scope(item.get("scope"))[0],
                }
            )
        )
        return row

    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_pack_row", leaking_row)
        m.setattr(
            pcm,
            "_legacy_pack_applies",
            lambda pack, *, include_all, referenced, keywords: bool(
                include_all or pack.get("id") in referenced or pcm._is_global_item(pack) or pcm._paths_from_item(pack)
            ),
        )
        assert run_oracle(), "legacy pack modern-field leak must be caught"
    assert run_oracle() == []

    # leak 3: modern relevance suppression (keyword recovery silently removed for legacy)
    with monkeypatch.context() as m:
        m.setattr(pcm, "_legacy_relevance_match", lambda item, keywords: False)
        assert run_oracle(), "dropping legacy relevance recovery must be caught"
    assert run_oracle() == []


def test_oracle_catches_plausible_wrong_legacy_implementations(monkeypatch: pytest.MonkeyPatch) -> None:
    """The kill map is single-fault; this proves the corpus also rejects near-miss wrong implementations
    (adversarial review round): each variant is injected into one production seam and must turn the oracle RED."""
    assert run_oracle() == []
    original_global = pcm.classify_scope
    original_relevance = pcm._legacy_relevance_match
    original_item_paths = pcm._item_scope_paths

    def without_carrier(carrier: str):
        def patched(item: dict[str, Any]) -> tuple[set[str], bool]:
            return original_item_paths({k: v for k, v in item.items() if k != carrier})

        return patched

    variants: dict[str, tuple[str, Any]] = {
        "explicit_ref_case_insensitive": ("_legacy_contract_explicit_ref", lambda c, ref: str(c.get("id") or "").lower() in {r.lower() for r in ref}),
        "explicit_ref_substring": ("_legacy_contract_explicit_ref", lambda c, ref: any(r in str(c.get("id") or "") for r in ref)),
        "explicit_ref_description": ("_legacy_contract_explicit_ref", lambda c, ref: c.get("id") in ref or c.get("description") in ref),
        "pack_explicit_ref_case_insensitive": ("_legacy_pack_explicit_ref", lambda p, ref: str(p.get("id") or "").lower() in {r.lower() for r in ref}),
        "relevance_case_sensitive": ("_legacy_relevance_match", lambda i, kw: bool(kw) and any(k in ((i.get("id") or "") + " " + (i.get("description") or "")) for k in kw)),
        "relevance_off_for_scoped_rules": ("_legacy_relevance_match", lambda i, kw: False if (i.get("paths") or i.get("scope") or i.get("patterns")) else original_relevance(i, kw)),
        "relevance_word_boundary": ("_legacy_relevance_match", lambda i, kw: any(f" {k} " in f" {(i.get('id') or '')} {(i.get('description') or '')} ".lower() for k in kw)),
        "global_contains": ("classify_scope", lambda v: (isinstance(v, str) and "global" in v.lower(), True)),
        "global_all_document": ("classify_scope", lambda v: (original_global(v)[0] or (isinstance(v, str) and v.strip().lower() in {"all", "document"}), True)),
        "paths_ignore_files_carrier": ("_item_scope_paths", without_carrier("files")),
        "paths_ignore_related_files_carrier": ("_item_scope_paths", without_carrier("related_files")),
        "paths_ignore_file_path_carrier": ("_item_scope_paths", without_carrier("file_path")),
        "paths_ignore_path_carrier": ("_item_scope_paths", without_carrier("path")),
    }
    for name, (seam, replacement) in variants.items():
        with monkeypatch.context() as m:
            m.setattr(pcm, seam, replacement)
            assert run_oracle(), f"wrong legacy implementation not caught: {name}"
        assert run_oracle() == [], name

    with monkeypatch.context() as m:
        original_legacy_selected = pcm.legacy_pack_matches_selected

        def reverse_alias(pack: dict[str, Any], selected: str) -> bool:
            return original_legacy_selected(pack, selected) or bool(selected) and selected.lower().endswith("-" + str(pack.get("id") or "").lower())

        m.setattr(pcm, "legacy_pack_matches_selected", reverse_alias)
        assert run_oracle(), "reverse-alias legacy selection not caught"
    assert run_oracle() == []


def test_oracle_catches_legacy_row_projection_leaks_and_display_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whole-row observation (adversarial review round 2): a legacy row that gains or loses ANY field, or a
    display-normalization change, is an observable divergence."""
    assert run_oracle() == []
    original_row = pcm._legacy_contract_row
    original_pack_row = pcm._legacy_pack_row
    original_sanitize = pcm.sanitize_display_path

    def with_sections(item: dict[str, Any]) -> dict[str, Any]:
        row = original_row(item)
        row["sections"] = {"x": [{"text": "leak"}]}
        return row

    def without_scope_and_paths(item: dict[str, Any]) -> dict[str, Any]:
        row = original_row(item)
        for key in ("scope", "paths", "patterns", "is_global"):
            row.pop(key, None)
        return row

    def pack_with_paths(item: dict[str, Any]) -> dict[str, Any]:
        row = original_pack_row(item)
        if item.get("paths"):
            row["paths"] = list(item["paths"])
        return row

    def pack_with_description_default(item: dict[str, Any]) -> dict[str, Any]:
        row = original_pack_row(item)
        row["description"] = row.get("description") or row.get("id")
        return row

    def no_redaction(path: str) -> str:
        return path.strip()

    def no_backslash(path: str) -> str:
        return original_sanitize(path.replace("\\", "\x00")).replace("\x00", "\\")

    variants = {
        ("_legacy_contract_row", with_sections): "contract row gains sections",
        ("_legacy_contract_row", without_scope_and_paths): "contract row loses scope/paths/patterns",
        ("_legacy_pack_row", pack_with_paths): "pack row gains paths",
        ("_legacy_pack_row", pack_with_description_default): "pack row fabricates description",
        ("sanitize_display_path", no_redaction): "display redaction removed",
        ("sanitize_display_path", no_backslash): "backslash normalization removed",
        ("_legacy_relevance_match", lambda i, kw: any(k in ((i.get("id") or "") + (i.get("description") or "")).lower() for k in kw)): "relevance text joined without separator",
    }
    for (seam, replacement), name in variants.items():
        with monkeypatch.context() as m:
            m.setattr(pcm, seam, replacement)
            assert run_oracle(), f"not caught: {name}"
        assert run_oracle() == [], name


def test_oracle_catches_projection_and_canonicalization_mutants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-3 F2: wrong legacy implementations in the row-projection / canonicalization layer are caught."""
    assert run_oracle() == []
    original_row = pcm._legacy_contract_row
    original_pack_row = pcm._legacy_pack_row

    def mutate(row_builder, fn):
        def patched(item: dict[str, Any]) -> dict[str, Any]:
            return fn(row_builder(item))

        return patched

    def unsorted_paths(value: Any) -> list[str]:
        return [item for item in [pcm.sanitize_display_path(x) for x in value if isinstance(x, str) and x.strip()] if item] if isinstance(value, list) else []

    variants: list[tuple[str, str, Any]] = [
        ("unsorted_paths", "_sanitize_contract_paths", unsorted_paths),
        ("unsorted_patterns", "_normalized_contract_patterns", lambda v: [pcm.sanitize_display_path(x.strip()) for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []),
        ("sanitize_no_strip", "sanitize_display_path", lambda p: p.replace("\\", "/") if p.strip() else ""),
        ("tilde_any_redacted", "sanitize_display_path", lambda p: "[LOCAL_PATH_REDACTED]" if p.strip().startswith("~") else p.strip()),
        ("contract_id_lowercased", "_legacy_contract_row", mutate(original_row, lambda r: {**r, **({"id": r["id"].lower()} if r.get("id") else {})})),
        ("pack_preset_lowercased", "_legacy_pack_row", mutate(original_pack_row, lambda r: {**r, "recommended_review_preset": (r["recommended_review_preset"] or "").lower() or None})),
        ("pack_drops_null_keys", "_legacy_pack_row", mutate(original_pack_row, lambda r: {k: v for k, v in r.items() if v is not None})),
        ("contract_description_collapsed", "_legacy_contract_row", mutate(original_row, lambda r: {**r, **({"description": " ".join(r["description"].split())} if r.get("description") else {})})),
        ("star_pattern_substring", "_matches_legacy_pattern", lambda path, patterns: any((p.strip()[:-1] in path) if p.strip().endswith("*") else (p.strip() in path) for p in patterns if p.strip())),
    ]
    for name, seam, replacement in variants:
        with monkeypatch.context() as m:
            m.setattr(pcm, seam, replacement)
            assert run_oracle(), f"not caught: {name}"
        assert run_oracle() == [], name
