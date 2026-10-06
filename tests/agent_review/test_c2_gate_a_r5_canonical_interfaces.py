"""Gate A cycle-4 closure (PR #372, review 5423621402): canonical interfaces.

R5-A CanonicalProducerCarrierClosure  4191379306   Declared(A) AND ConsumerCanRead(A) != CarrierPreserves(A)
R5-B DeterministicSemanticProjection  4191379311   SemanticEquality -> CanonicalArtifactEquality
R5-C StrictJsonAdmissionDomain        4191379316   AdmittedScalar -> StrictJsonScalar (finite)

Test taxonomy (Cycle-4 lesson): ConsumerCapability is NOT qualified until
CanonicalProducer -> TypedCarrier -> SerializedIntake -> Consumer has a positive AND a negative control.
  * canonical controls (`test_cm_r5a_*`, `test_pc_r5a_*`, the R5-B permutation controls) MUST begin from real
    files under `.aiops/` and traverse load_repo_profile -> build_intake -> model_dump -> planner.
  * defensive controls (`test_dc_r5a_*`) may construct a raw intake and DO NOT prove canonical support.
"""

from __future__ import annotations

import ast
import copy
import itertools
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agent_review import chunk_payload_builder, payload_cost_model as pcm, repo_profile
from app.agent_review.chunk_payload_builder import build_chunk_payloads
from app.agent_review.cli import build_intake
from app.agent_review.repo_profile import REPO_PROFILE_UNSUPPORTED_C2_FIELDS, load_repo_profile
from app.agent_review.schemas import ReviewIntake
from app.agent_review.semantic_chunker import build_semantic_chunk_plan
from tests.agent_review.test_c2_executable_contract_gate_a import _base_intake, _brief

REPO_ROOT = Path(__file__).resolve().parents[2]
THIS_FILE = Path(__file__)

DIFF = "\n".join(
    [
        "diff --git a/backend/api/shifts.py b/backend/api/shifts.py",
        "index 111..222 100644",
        "--- a/backend/api/shifts.py",
        "+++ b/backend/api/shifts.py",
        "@@ -1,2 +1,3 @@",
        " def f():",
        "-    return 1",
        "+    return 2",
    ]
) + "\n"
ARTIFACTS = (
    "artifacts:\n"
    "  - {name: full-diff, path: full.diff, kind: diff, required: true}\n"
    "  - {name: file-diff-context, path: file-diff-context.json, kind: json}\n"
)
BASE_CONTRACTS = {"c1": {"description": "d1", "paths": ["backend/api/*"], "rules": ["r1"]}, "c2": ["rule two"], "c3": ["rule three"]}


# ---------------------------------------------------------------------------
# Canonical producer harness: real files -> load_repo_profile -> build_intake -> planner -> payloads
# ---------------------------------------------------------------------------


def _write_repo(tmp_path: Path, name: str, *, profile_extra: str = "", packs: Any = None, contracts: Any = None) -> Path:
    repo = tmp_path / name
    (repo / ".aiops").mkdir(parents=True)
    (repo / ".aiops" / "repo-profile.yaml").write_text("target_repo: example/target\n" + ARTIFACTS + profile_extra, encoding="utf-8")
    if packs is not None:
        (repo / ".aiops" / "review-packs.yaml").write_text(packs if isinstance(packs, str) else yaml.safe_dump(packs, sort_keys=False), encoding="utf-8")
    if contracts is not None:
        (repo / ".aiops" / "domain-contracts.yaml").write_text(contracts if isinstance(contracts, str) else yaml.safe_dump(contracts, sort_keys=False), encoding="utf-8")
    return repo


def _canonical_run(tmp_path: Path, name: str, **repo_kwargs: Any) -> tuple[ReviewIntake, Any, dict[str, Any]]:
    repo = _write_repo(tmp_path, name, **repo_kwargs)
    agent = tmp_path / f"{name}-agent"
    agent.mkdir()
    (agent / "full.diff").write_text(DIFF, encoding="utf-8")
    (agent / "file-diff-context.json").write_text(
        json.dumps({"files": [{"path": "backend/api/shifts.py", "status": "modified", "summary": "x"}]}), encoding="utf-8"
    )
    intake, _ = build_intake(target_repo="example/target", repo_root=repo, agent_dir=agent)
    serialized = ReviewIntake.model_validate(json.loads(json.dumps(intake.model_dump(mode="json", exclude_none=True))))
    plan = build_semantic_chunk_plan(serialized.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    payloads: dict[str, Any] = {}
    if serialized.status != "failed" and plan.chunks:
        _, payloads = build_chunk_payloads(intake=serialized, chunk_plan=plan, pr_brief=_brief(serialized, plan), checks=None, validation_evidence=None)
    return serialized, plan, payloads


def _strip_clock(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_clock(v) for k, v in value.items() if k != "created_at"}
    if isinstance(value, list):
        return [_strip_clock(v) for v in value]
    return value


def _artifact_bytes(intake: ReviewIntake, plan: Any, payloads: dict[str, Any]) -> str:
    return pcm.canonical_json(
        _strip_clock(
            {
                "intake_status": intake.status,
                "intake_limits": intake.limitations,
                "plan": plan.model_dump(mode="json"),
                "payloads": {k: v.model_dump(mode="json") for k, v in sorted(payloads.items())},
            }
        )
    )


def _shuffled(value: Any, rng: random.Random) -> Any:
    if isinstance(value, dict):
        keys = list(value)
        rng.shuffle(keys)
        return {k: _shuffled(value[k], rng) for k in keys}
    if isinstance(value, list):
        return [_shuffled(v, rng) for v in value]
    return value


def _strict_json(obj: Any) -> str:
    """TEST-ONLY strict-JSON oracle (never production serialization)."""
    return json.dumps(obj, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _nonfinite_paths(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, float):
        return [] if math.isfinite(value) else [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _nonfinite_paths(v, f"{path}.{k}")]
    if isinstance(value, (list, tuple)):
        return [p for i, v in enumerate(value) for p in _nonfinite_paths(v, f"{path}[{i}]")]
    if hasattr(value, "model_dump"):
        return _nonfinite_paths(value.model_dump(mode="python"), path)
    return []


# ---------------------------------------------------------------------------
# R5-A -- canonical producer / carrier closure
# ---------------------------------------------------------------------------

CONSUMER_ONLY_KEYS = {"contracts": ["contract:missing"], "contract_refs": ["contract:missing"], "contract_bindings": {"p1": ["c1"]}}
INLINE_SOURCE_KEYS = {"domain_contracts": {"c1": ["inline rule"]}, "review_packs": {"packs": {"p1": {"description": "d"}}}}
ALL_REJECTED = {**CONSUMER_ONLY_KEYS, **INLINE_SOURCE_KEYS}


def _profile_with(key: str, value: Any) -> str:
    return yaml.safe_dump({key: value}, sort_keys=False)


def _assert_rejected_canonically(tmp_path: Path, key: str, value: Any, tag: str) -> None:
    repo = _write_repo(tmp_path, f"rej-{tag}", profile_extra=_profile_with(key, value), contracts=BASE_CONTRACTS)
    loaded = load_repo_profile(repo, target_repo="example/target")
    assert loaded.status == "failed" and loaded.error_class == "profile_invalid", "never silently absent"
    assert loaded.limitations == [f"repo_profile_unsupported_c2_field:{key}"], "typed and distinguishable"
    intake, plan, payloads = _canonical_run(tmp_path, f"rej-run-{tag}", profile_extra=_profile_with(key, value), contracts=BASE_CONTRACTS)
    assert intake.status == "failed" and intake.error_class == "profile_invalid"
    assert f"repo_profile_unsupported_c2_field:{key}" in intake.limitations
    assert intake.completeness["profile_loaded"] is False
    assert plan.status != "complete" and payloads == {}, "the review cannot stay conclusive"


@pytest.mark.parametrize("value_kind", ["declared", "empty_list", "null"])
@pytest.mark.parametrize("key", list(CONSUMER_ONLY_KEYS))
def test_cm_r5a_repo_profile_consumer_only_c2_keys_are_rejected(tmp_path: Path, key: str, value_kind: str) -> None:
    """CM-R5A-REPO-PROFILE-{CONTRACTS,CONTRACT-REFS,CONTRACT-BINDINGS}-REJECTED (canonical, from real files)."""
    value = {"declared": CONSUMER_ONLY_KEYS[key], "empty_list": [], "null": None}[value_kind]
    _assert_rejected_canonically(tmp_path, key, value, f"{key}-{value_kind}")


@pytest.mark.parametrize("key", list(INLINE_SOURCE_KEYS))
def test_cm_r5a_repo_profile_inline_c2_sources_are_rejected(tmp_path: Path, key: str) -> None:
    """Sibling: an inline source is overwritten by the file loader, so it would be dropped silently."""
    _assert_rejected_canonically(tmp_path, key, INLINE_SOURCE_KEYS[key], f"inline-{key}")


def test_cm_r5a_every_forbidden_field_is_reported_in_a_fixed_order(tmp_path: Path) -> None:
    profile = yaml.safe_dump({k: v for k, v in reversed(list(ALL_REJECTED.items()))}, sort_keys=False)
    loaded = load_repo_profile(_write_repo(tmp_path, "all", profile_extra=profile), target_repo="example/target")
    assert loaded.limitations == [f"repo_profile_unsupported_c2_field:{k}" for k in REPO_PROFILE_UNSUPPORTED_C2_FIELDS]
    assert set(REPO_PROFILE_UNSUPPORTED_C2_FIELDS) == set(ALL_REJECTED)


def test_cm_r5a_target_profile_grammar_is_not_expanded() -> None:
    """The decision is a REJECTION, not a new public grammar: TargetProfile / ReviewIntake keep their fields."""
    from app.agent_review.schemas import TargetProfile

    assert not ({"contracts", "contract_refs", "contract_bindings"} & set(TargetProfile.model_fields))
    assert not ({"contracts", "contract_refs", "contract_bindings"} & set(ReviewIntake.model_fields))


def test_cm_r5a_cli_exit_code_is_nonzero_for_a_rejected_profile(tmp_path: Path) -> None:
    """The fail-closed profile error is not weakened to keep the CLI at exit 0."""
    from tests.agent_review.test_aiops_review_intake_cli import _dev_env, _run_cli

    repo = _write_repo(tmp_path, "cli", profile_extra="contracts:\n  - contract:missing\n")
    out = tmp_path / "out" / "intake.json"
    result = _run_cli(repo, out, tmp_path / "out" / "report.json", env=_dev_env())
    assert result.returncode == 1, result.stdout + result.stderr
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["status"] == "failed" and "repo_profile_unsupported_c2_field:contracts" in written["limitations"]


def _canonical_intake(tmp_path: Path, name: str, packs: Any, contracts: Any) -> tuple[ReviewIntake, Any]:
    intake, plan, _ = _canonical_run(tmp_path, name, packs=packs, contracts=contracts)
    return intake, plan


def _context(intake: ReviewIntake, selected: str | None) -> tuple[dict[str, Any], list[str]]:
    refs, _ = pcm.parse_contract_refs(intake)
    return pcm.contracts_context(
        intake, chunk_files=["backend/api/shifts.py"], chunk_contracts=refs, chunk_id="chunk-1", selected_contract_pack=selected, semantic_group="other"
    )


CANONICAL_CONTRACTS = {"c1": ["rule one"], "c2": ["rule two"], "c3": ["rule three"]}


def test_pc_r5a_domain_contract_canonical_carrier(tmp_path: Path) -> None:
    """PC-R5A-DOMAIN-CONTRACT-CANONICAL-CARRIER: review-packs.yaml + domain-contracts.yaml survive to the consumer."""
    packs = {"packs": {"p1": {"description": "pack one", "domain_contract": "c1"}}}
    intake, plan = _canonical_intake(tmp_path, "pc-dc", packs, CANONICAL_CONTRACTS)
    assert intake.status == "complete" and not [lim for lim in plan.limitations if "unresolved" in lim]
    ctx, limits = _context(intake, "p1")
    assert not [lim for lim in limits if "unresolved" in lim or "orphan" in lim]
    pack = next(p for p in ctx["review_packs"] if p["id"] == "p1")
    assert pack["effective_contracts"] == ["c1"] and pack["required"] is True
    assert [c["id"] for c in ctx["domain_contracts"] if c.get("required")] == ["c1"]


def test_pc_r5a_contract_bindings_canonical_carrier(tmp_path: Path) -> None:
    """PC-R5A-CONTRACT-BINDINGS-CANONICAL-CARRIER: the review-packs ENVELOPE binding is the supported carrier."""
    packs = {"packs": {"p1": {"description": "pack one", "domain_contract": "c1"}}, "contract_bindings": {"p1": ["c2"]}}
    intake, plan = _canonical_intake(tmp_path, "pc-cb", packs, CANONICAL_CONTRACTS)
    assert intake.status == "complete" and not [lim for lim in plan.limitations if "unresolved" in lim or "orphan" in lim]
    ctx, _ = _context(intake, "p1")
    assert next(p for p in ctx["review_packs"] if p["id"] == "p1")["effective_contracts"] == ["c1", "c2"]
    assert sorted(c["id"] for c in ctx["domain_contracts"] if c.get("required")) == ["c1", "c2"]


def test_cm_r5a_canonical_relations_that_do_not_resolve_stay_visible_at_the_plan(tmp_path: Path) -> None:
    """Negative twin of the positives above: the same canonical carriers, with a missing identity, fail closed."""
    packs = {"packs": {"p1": {"description": "d", "domain_contract": "missing-c"}}, "contract_bindings": {"p1": ["missing-b"], "ghost": ["c1"]}}
    _, plan = _canonical_intake(tmp_path, "cm-unres", packs, CANONICAL_CONTRACTS)
    for limitation in ("unresolved_contract_binding:p1:missing-c", "unresolved_contract_binding:p1:missing-b", "orphan_contract_binding:ghost"):
        assert limitation in plan.limitations, limitation


def test_dc_r5a_raw_intake_explicit_ref(tmp_path: Path) -> None:
    """DC-R5A-RAW-INTAKE-EXPLICIT-REF: DEFENSIVE compatibility on a raw dict. Not canonical-producer proof."""
    refs, limits = pcm.parse_contract_refs({"target_profile": {}, "contracts": ["contract:x"], "contract_refs": ["contract:y"]})
    assert refs == ["contract:x", "contract:y"] and limits == []
    typed = _base_intake()
    typed_dump = typed.model_dump(mode="json")
    assert "contracts" not in typed_dump and "contract_refs" not in typed_dump, "the canonical typed producer cannot carry them"
    refs, _ = pcm.parse_contract_refs(ReviewIntake.model_validate({**typed_dump, "contracts": ["contract:x"]}))
    assert "contract:x" not in refs, "a typed ReviewIntake drops the raw top-level declaration (so it is not canonical)"


def test_dc_r5a_raw_profile_extra_bindings() -> None:
    """DC-R5A-RAW-PROFILE-EXTRA-BINDINGS: profile-level `contract_bindings` works on a hand-built intake only."""
    intake = _base_intake()
    intake.target_profile = {"domain_contracts": CANONICAL_CONTRACTS, "review_packs": {"packs": {"p1": {"description": "d"}}}, "contract_bindings": {"p1": ["c3"]}}
    ctx, _ = pcm.contracts_context(
        intake, chunk_files=["x.py"], chunk_contracts=["target_profile:review_packs"], chunk_id="c", selected_contract_pack="p1", semantic_group="other"
    )
    assert next(p for p in ctx["review_packs"] if p["id"] == "p1")["effective_contracts"] == ["c3"]


C2_INPUT_SURFACE_MATRIX: dict[str, dict[str, Any]] = {
    "repo_profile.contracts": {"canonical_producer": "REJECTED", "consumer_raw_support": "defensive: parse_contract_refs(target_profile)", "authority": "no owner authority; freeze; TargetProfile v1 unchanged"},
    "repo_profile.contract_refs": {"canonical_producer": "REJECTED", "consumer_raw_support": "defensive: parse_contract_refs(target_profile)", "authority": "no owner authority; freeze; TargetProfile v1 unchanged"},
    "repo_profile.contract_bindings": {"canonical_producer": "REJECTED", "consumer_raw_support": "defensive: normalize_review_packs(extra_bindings)", "authority": "canonical carrier is the review-packs envelope; REVIEW_PACK_LEGACY_INPUT_POLICY"},
    "repo_profile.domain_contracts (inline)": {"canonical_producer": "REJECTED", "consumer_raw_support": "n/a (TargetProfile field, loaded from .aiops/domain-contracts.yaml)", "authority": "docs/AGENT_REVIEW_ENGINE.md carriers"},
    "repo_profile.review_packs (inline)": {"canonical_producer": "REJECTED", "consumer_raw_support": "n/a (TargetProfile field, loaded from .aiops/review-packs.yaml)", "authority": "docs/AGENT_REVIEW_ENGINE.md carriers"},
    "review_packs.domain_contract": {"canonical_producer": "SUPPORTED", "canonical_consumer": "SUPPORTED", "authority": "02_OBLIGATION_MATRIX OBL-CL2; PC-R5A-DOMAIN-CONTRACT-CANONICAL-CARRIER"},
    "review_packs.contract_bindings": {"canonical_producer": "SUPPORTED", "canonical_consumer": "SUPPORTED", "authority": "02_OBLIGATION_MATRIX OBL-CL2; PC-R5A-CONTRACT-BINDINGS-CANONICAL-CARRIER"},
    "raw_intake.contracts": {"canonical": False, "defensive_support": "parse_contract_refs(raw dict); dropped by typed ReviewIntake", "authority": "NONCANONICAL_DEFENSIVE_COMPATIBILITY"},
    "raw_intake.contract_refs": {"canonical": False, "defensive_support": "parse_contract_refs(raw dict); dropped by typed ReviewIntake", "authority": "NONCANONICAL_DEFENSIVE_COMPATIBILITY"},
}


def test_r5a_c2_input_surface_matrix_has_no_ambiguous_cell_and_is_executable(tmp_path: Path) -> None:
    for surface, row in C2_INPUT_SURFACE_MATRIX.items():
        assert row["authority"]
        assert ("canonical_producer" in row) != ("canonical" in row)
        if "canonical_producer" in row:
            assert row["canonical_producer"] in {"REJECTED", "SUPPORTED"}
            if row["canonical_producer"] == "SUPPORTED":
                assert row["canonical_consumer"] == "SUPPORTED"
        else:
            assert row["canonical"] is False and row["defensive_support"]
    rejected = {s.split(".")[1].split(" ")[0] for s, r in C2_INPUT_SURFACE_MATRIX.items() if s.startswith("repo_profile.") and r["canonical_producer"] == "REJECTED"}
    assert rejected == set(REPO_PROFILE_UNSUPPORTED_C2_FIELDS)
    for key in rejected:
        assert load_repo_profile(_write_repo(tmp_path, f"m-{key}", profile_extra=_profile_with(key, ALL_REJECTED[key])), target_repo="example/target").status == "failed"


def test_r5a_every_profile_key_the_consumer_reads_has_a_deliberate_classification() -> None:
    """R5-A census guard: a new `profile.get("<key>")` read in the consumer must be classified here first."""
    tree = ast.parse((REPO_ROOT / "app" / "agent_review" / "payload_cost_model.py").read_text(encoding="utf-8"))
    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and isinstance(node.func.value, ast.Name) and node.func.value.id == "profile":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                read.add(node.args[0].value)
    from app.agent_review.schemas import TargetProfile

    assert read == {"domain_contracts", "review_packs", "contract_bindings", "artifacts"}, read
    for key in ("domain_contracts", "review_packs", "artifacts"):
        assert key in TargetProfile.model_fields, "carried by the canonical typed producer"
    # `contracts` / `contract_refs` are read through the fixed tuple of parse_contract_refs
    assert "('contracts', 'contract_refs')" in {ast.unparse(n.iter) for n in ast.walk(tree) if isinstance(n, ast.For)}
    for key in ("contract_bindings", "contracts", "contract_refs"):
        assert key in REPO_PROFILE_UNSUPPORTED_C2_FIELDS and key not in TargetProfile.model_fields


def test_r5a_taxonomy_canonical_controls_begin_from_real_files() -> None:
    """Cycle-4 method rule, executable: canonical R5-A controls use the real-file harness and never construct
    a ReviewIntake directly; defensive `dc_*` controls are the only ones that may."""
    tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
    source = THIS_FILE.read_text(encoding="utf-8")
    checked = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(("test_cm_r5a_", "test_pc_r5a_")):
            body = ast.get_source_segment(source, node) or ""
            uses_files = any(token in body for token in ("_canonical_run(", "_canonical_intake(", "_write_repo(", "_assert_rejected_canonically("))
            builds_raw = any(token in body for token in ("_base_intake(", "ReviewIntake.model_validate(", "ReviewIntake("))
            if node.name in {"test_cm_r5a_target_profile_grammar_is_not_expanded"}:
                continue
            assert uses_files and not builds_raw, node.name
            checked += 1
    assert checked == 7


def test_ab_r5a_canonical_producer_drop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AB-R5A-CANONICAL-PRODUCER-DROP: restore the silent ignore of a forbidden canonical field."""

    counter = itertools.count()

    def witness() -> bool:
        repo = _write_repo(tmp_path, f"ab-{next(counter)}", profile_extra="contracts:\n  - contract:missing\n", contracts=BASE_CONTRACTS)
        loaded = load_repo_profile(repo, target_repo="example/target")
        return loaded.status == "failed" and loaded.limitations == ["repo_profile_unsupported_c2_field:contracts"]

    assert witness() is True
    with monkeypatch.context() as m:
        m.setattr(repo_profile, "REPO_PROFILE_UNSUPPORTED_C2_FIELDS", ())
        assert witness() is False, "mutant must go RED: the declaration silently disappears"
        silent = load_repo_profile(_write_repo(tmp_path, "ab-silent", profile_extra="contracts:\n  - contract:missing\n"), target_repo="example/target")
        assert silent.status == "complete" and silent.limitations == [] and "contracts" not in silent.profile.model_dump()
    assert witness() is True


# ---------------------------------------------------------------------------
# R5-B -- deterministic semantic projection
# ---------------------------------------------------------------------------

PERMUTATION_SCENARIOS: dict[str, tuple[Any, Any]] = {
    "orphan_bindings": ({"packs": {"p1": {"description": "d"}}, "contract_bindings": {"zz": ["c1"], "aa": ["c2"], "mm": ["c3"]}}, BASE_CONTRACTS),
    "unresolved_pack_domain_contract": ({"packs": {"p1": {"domain_contract": "x1"}, "p2": {"domain_contract": "x2"}, "p3": {"domain_contract": "x3"}}}, BASE_CONTRACTS),
    "unresolved_binding_refs": ({"packs": {"p1": {"description": "a"}, "p2": {"description": "b"}, "p3": {"description": "c"}}, "contract_bindings": {"p1": ["m1"], "p2": ["m2"], "p3": ["m3"]}}, BASE_CONTRACTS),
    "multi_bad_binding_values": ({"packs": {"p1": {"description": "a"}, "p2": {"description": "b"}}, "contract_bindings": {"p1": "notalist", "p2": [1], "zz": {"a": 1}}}, BASE_CONTRACTS),
    "multi_invalid_contracts": ({"packs": {"p1": {"description": "a"}}}, {"c1": {"paths": ["/abs"], "rules": ["r"]}, "c2": {"description": 5}, "c3": 7, "c4": {"id": "other"}}),
    "multi_invalid_packs": ({"packs": {"p1": {"paths": ["/abs"]}, "p2": {"scope": 3}, "p3": {"unknown_key": 1}}}, BASE_CONTRACTS),
    "valid_multi": ({"packs": {"p1": {"domain_contract": "c1"}, "p2": {"domain_contract": "c2"}, "p3": {"description": "x"}}, "contract_bindings": {"p3": ["c3", "c2"], "p1": ["c3"]}}, BASE_CONTRACTS),
}
PERMUTATION_SEEDS = range(6)


def _permuted_artifacts(tmp_path: Path, name: str, packs: Any, contracts: Any, seed: int) -> tuple[str, list[str]]:
    rng = random.Random(seed)
    shuffled_packs, shuffled_contracts = yaml.safe_dump(_shuffled(packs, rng), sort_keys=False), yaml.safe_dump(_shuffled(contracts, rng), sort_keys=False)
    intake, plan, payloads = _canonical_run(tmp_path, f"{name}-{seed}", packs=shuffled_packs, contracts=shuffled_contracts)
    return _artifact_bytes(intake, plan, payloads), list(plan.limitations)


@pytest.mark.parametrize("scenario", list(PERMUTATION_SCENARIOS))
def test_r5b_yaml_key_order_never_changes_plan_or_payload_bytes(tmp_path: Path, scenario: str) -> None:
    packs, contracts = PERMUTATION_SCENARIOS[scenario]
    results = [_permuted_artifacts(tmp_path, scenario, packs, contracts, seed) for seed in PERMUTATION_SEEDS]
    assert len({r[0] for r in results}) == 1, "CanonicalPlanBytes(A) == CanonicalPlanBytes(B) for every key order"
    assert len({tuple(r[1]) for r in results}) == 1, "limitation order byte/order exact"


def test_r5b_three_orphans_all_six_permutations_are_byte_identical(tmp_path: Path) -> None:
    keys = ["zz", "aa", "mm"]
    seen: set[str] = set()
    orders: set[tuple[str, ...]] = set()
    for perm in itertools.permutations(keys):
        packs = "packs:\n  p1:\n    description: d\ncontract_bindings:\n" + "".join(f"  {k}:\n    - c1\n" for k in perm)
        intake, plan, payloads = _canonical_run(tmp_path, "orph-" + "".join(perm), packs=packs, contracts=BASE_CONTRACTS)
        seen.add(_artifact_bytes(intake, plan, payloads))
        orders.add(tuple(lim for lim in plan.limitations if lim.startswith("orphan_contract_binding")))
    assert len(list(itertools.permutations(keys))) == 6 and len(seen) == 1
    assert orders == {("orphan_contract_binding:aa", "orphan_contract_binding:mm", "orphan_contract_binding:zz")}


def test_r5b_deterministic_items_orders_by_normalized_identity_and_never_crashes_on_mixed_keys() -> None:
    items = pcm._deterministic_items({"b": 1, " a ": 2, 3: 3, "a": 4, None: 5})
    assert [k for k, _ in items][:3] == [" a ", "a", "b"] and {k for k, _ in items[3:]} == {3, None}
    assert pcm._deterministic_items({"b": 1, "a": 2}) == pcm._deterministic_items({"a": 2, "b": 1})


C2_LIMITATION_DETERMINISM_CENSUS: dict[tuple[str, str], dict[str, str]] = {
    ("parse_contract_refs", "('contracts', 'contract_refs')"): {"collection": "fixed tuple", "order_authority": "declared tuple", "deterministic": "yes", "action": "none"},
    ("parse_contract_refs", "val"): {"collection": "profile/intake list (declared sequence)", "order_authority": "list order is semantic; _dedupe keeps first", "deterministic": "yes", "action": "none"},
    ("contracts_context", "chunk_contracts"): {"collection": "planner-supplied list", "order_authority": "planner order", "deterministic": "yes", "action": "none"},
    ("contracts_context", "packs"): {"collection": "normalized pack rows", "order_authority": "rows sorted by (id, description)", "deterministic": "yes", "action": "none"},
    ("contracts_context", "p['effective_contracts']"): {"collection": "set-derived", "order_authority": "sorted(eff_refs)", "deterministic": "yes", "action": "none"},
    ("contracts_context", "sorted(bindings)"): {"collection": "orphan contract_bindings mapping", "order_authority": "sorted identity (R5-B)", "deterministic": "yes", "action": "FIXED (4191379311)"},
    ("contracts_context", "sorted(explicit_contract_refs - set(contracts_by_id.keys()))"): {"collection": "set difference", "order_authority": "sorted", "deterministic": "yes", "action": "none"},
    ("normalize_review_packs", "carriers"): {"collection": "fixed [envelope, profile] list", "order_authority": "declared order", "deterministic": "yes", "action": "none"},
    ("normalize_review_packs", "parsed_carriers"): {"collection": "fixed carrier list", "order_authority": "declared order", "deterministic": "yes", "action": "none"},
    ("normalize_review_packs", "parsed.items()"): {"collection": "binding mapping", "order_authority": "single constant reason (conflicting_carriers)", "deterministic": "yes", "action": "none (reason is key-order independent)"},
    ("checks_context", "checks_rows"): {"collection": "checks list", "order_authority": "out of C2 scope", "deterministic": "n/a", "action": "out_of_scope"},
    ("_filter_lci", "_list(document.get('confirmed_local_failures'))"): {"collection": "LCI list", "order_authority": "out of C2 scope", "deterministic": "n/a", "action": "out_of_scope"},
    ("artifact_state_limitations", "intake.artifact_status"): {"collection": "artifact status list", "order_authority": "out of C2 scope", "deterministic": "n/a", "action": "out_of_scope"},
    ("project_min_hunk_preserving_chars", "chunk_files_sorted"): {"collection": "sorted files", "order_authority": "sorted", "deterministic": "yes", "action": "none"},
}


def test_r5b_every_limitation_emitting_loop_is_classified_in_the_census() -> None:
    """Census guard: a new limitation-emitting `for` loop in payload_cost_model must be added with its order authority."""
    tree = ast.parse((REPO_ROOT / "app" / "agent_review" / "payload_cost_model.py").read_text(encoding="utf-8"))

    def emits(node: ast.AST) -> bool:
        return any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"append", "extend", "add", "update"} and "limit" in ast.unparse(n.func.value)
            for n in ast.walk(node)
        )

    found = {(fn.name, ast.unparse(n.iter)) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) for n in ast.walk(fn) if isinstance(n, ast.For) and emits(n)}
    assert found == set(C2_LIMITATION_DETERMINISM_CENSUS), (sorted(found - set(C2_LIMITATION_DETERMINISM_CENSUS)), sorted(set(C2_LIMITATION_DETERMINISM_CENSUS) - found))
    assert {v["deterministic"] for v in C2_LIMITATION_DETERMINISM_CENSUS.values()} <= {"yes", "n/a"}


def _orphan_limits(intake_module: Any, order: list[str]) -> list[str]:
    intake = _base_intake()
    intake.target_profile = {
        "domain_contracts": {"c1": ["a rule"]},
        "review_packs": {"packs": {"p": {"description": "d"}}, "contract_bindings": {k: ["c1"] for k in order}},
    }
    _, limits = intake_module.contracts_context(
        intake, chunk_files=["x.py"], chunk_contracts=["target_profile:review_packs"], chunk_id="c", selected_contract_pack=None, semantic_group="other"
    )
    return [lim for lim in limits if lim.startswith("orphan_contract_binding")]


def _mutant_module(mutations: list[tuple[str, str]], name: str) -> Any:
    import types

    source = (REPO_ROOT / "app" / "agent_review" / "payload_cost_model.py").read_text(encoding="utf-8")
    for old, new in mutations:
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    module = types.ModuleType(name)
    module.__file__ = f"<mutant:{name}>"
    sys.modules[name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 - test-only source mutation
    return module


def test_ab_r5b_orphan_binding_order() -> None:
    """AB-R5B-ORPHAN-BINDING-ORDER: insertion-order iteration makes the permutation control RED."""
    assert _orphan_limits(pcm, ["zz", "aa"]) == _orphan_limits(pcm, ["aa", "zz"])
    # defense in depth: the carrier is parsed in identity order AND the orphan loop sorts; the predecessor had neither
    only_loop = _mutant_module([("for bound_pid in sorted(bindings):", "for bound_pid in list(bindings):")], "_r5b_mutant_loop")
    assert _orphan_limits(only_loop, ["zz", "aa"]) == _orphan_limits(only_loop, ["aa", "zz"]), "parse order still protects"
    only_parse = _mutant_module([("    for key, value in _deterministic_items(raw):\n", "    for key, value in raw.items():\n")], "_r5b_mutant_parse")
    assert _orphan_limits(only_parse, ["zz", "aa"]) == _orphan_limits(only_parse, ["aa", "zz"]), "the sorted loop still protects"
    both = _mutant_module(
        [
            ("for bound_pid in sorted(bindings):", "for bound_pid in list(bindings):"),
            ("    for key, value in _deterministic_items(raw):\n", "    for key, value in raw.items():\n"),
        ],
        "_r5b_mutant_orphan",
    )
    assert _orphan_limits(both, ["zz", "aa"]) != _orphan_limits(both, ["aa", "zz"]), "predecessor behavior must go RED"


def test_ab_r5b_first_failure_iteration_order(monkeypatch: pytest.MonkeyPatch) -> None:
    doc_a = {"c1": {"paths": ["/abs"], "rules": ["r"]}, "c2": {"description": 5}}
    doc_b = dict(reversed(list(doc_a.items())))
    assert pcm.normalize_domain_contracts(doc_a)[3] == pcm.normalize_domain_contracts(doc_b)[3]
    with monkeypatch.context() as m:
        m.setattr(pcm, "_deterministic_items", lambda mapping: list(mapping.items()))
        assert pcm.normalize_domain_contracts(doc_a)[3] != pcm.normalize_domain_contracts(doc_b)[3], "mutant must go RED"
    assert pcm.normalize_domain_contracts(doc_a)[3] == pcm.normalize_domain_contracts(doc_b)[3]


# ---------------------------------------------------------------------------
# R5-C -- strict-JSON admission domain
# ---------------------------------------------------------------------------

NONFINITE = {"nan": float("nan"), "pos_inf": float("inf"), "neg_inf": float("-inf")}
FINITE = {"int": 7, "float": 1.5, "negative_float": -2.25, "large_float": 1.7e308, "tiny_float": 5e-324, "zero": 0.0}


def _expected_value_docs(value: Any) -> dict[str, Any]:
    return {
        "named_list": {"c1": [{"rule": "r", "expected_value": value}]},
        "rules_section": {"c1": {"description": "d", "paths": ["backend/api/*"], "rules": [{"rule": "r", "expected_value": value}]}},
        "slot_rules": {"c1": {"description": "d", "paths": ["backend/api/*"], "slot_rules": [{"rule": "r", "expected_value": value}]}},
    }


@pytest.mark.parametrize("form", ["named_list", "rules_section", "slot_rules"])
@pytest.mark.parametrize("kind", list(NONFINITE))
def test_cm_r5c_nonfinite_expected_value_is_invalid_at_source_admission(kind: str, form: str) -> None:
    """CM-R5C-{NAN,POS-INF,NEG-INF}-EXPECTED-VALUE: rejected before projection, in the existing source-state family."""
    rows, state, subtype, limits = pcm.normalize_domain_contracts(_expected_value_docs(NONFINITE[kind])[form])
    assert rows == [] and state == pcm.SOURCE_STATE_INVALID and subtype == pcm.SUBTYPE_MALFORMED_SHAPE
    assert limits == ["invalid_source_contract:MALFORMED_SHAPE"]


@pytest.mark.parametrize("form", ["named_list", "rules_section", "slot_rules"])
@pytest.mark.parametrize("kind", list(FINITE))
def test_pc_r5c_finite_expected_value_is_admitted_and_strict_json(kind: str, form: str) -> None:
    rows, state, _, limits = pcm.normalize_domain_contracts(_expected_value_docs(FINITE[kind])[form])
    assert state == pcm.SOURCE_STATE_PRESENT_VALID and limits == [] and rows
    assert _strict_json(rows) and _nonfinite_paths(rows) == []
    emitted = [s["expected_value"] for r in rows for sec in r["sections"].values() for s in sec if "expected_value" in s]
    assert emitted == [FINITE[kind]]


def test_pc_r5c_other_scalar_types_are_unchanged() -> None:
    for value in ("text", True, False, 0, -3):
        rows, state, _, _ = pcm.normalize_domain_contracts(_expected_value_docs(value)["named_list"])
        assert state == pcm.SOURCE_STATE_PRESENT_VALID and rows


def _payload_surfaces(profile: dict[str, Any]) -> dict[str, Any]:
    """Real production builders on a raw intake that KEEPS python floats (the only way a float reaches the carrier)."""
    intake = _base_intake()
    intake.target_profile = profile
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    manifest, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=_brief(intake, plan), checks=None, validation_evidence=None)
    ctx, _ = pcm.contracts_context(
        intake, chunk_files=["backend/api/shifts.py"], chunk_contracts=["target_profile:domain_contracts"], chunk_id="c", selected_contract_pack=None, semantic_group="other"
    )
    brief = _brief(intake, plan)
    return {
        "plan": plan,
        "brief": brief,
        "manifest": manifest,
        "contracts_context": ctx,
        "chunk_payloads": {k: {"chunk_context": v.chunk_context, "limitations": v.limitations} for k, v in payloads.items()},
        "payload_models": {k: v.model_dump(mode="python") for k, v in payloads.items()},
    }


def test_r5c_strict_json_oracle_over_every_admitted_gate_a_output_family() -> None:
    """SuccessfullyAdmittedGateAOutput -> StrictJsonSerializable (python-mode structures, allow_nan=False)."""
    profile = {"domain_contracts": _expected_value_docs(1.5)["rules_section"]}
    surfaces = _payload_surfaces(profile)
    assert set(surfaces) >= {"plan", "brief", "manifest", "contracts_context", "chunk_payloads", "payload_models"}
    for family, obj in surfaces.items():
        python_form = obj.model_dump(mode="python") if hasattr(obj, "model_dump") else obj
        assert _nonfinite_paths(python_form) == [], family
        assert _strict_json(python_form), family
    emitted = json.dumps(surfaces["chunk_payloads"], sort_keys=True)
    assert "1.5" in emitted, "the oracle really covers the emitted expected_value"


@pytest.mark.parametrize("kind", list(NONFINITE))
def test_cm_r5c_nonfinite_never_reaches_an_admitted_payload(kind: str) -> None:
    profile = {"domain_contracts": _expected_value_docs(NONFINITE[kind])["rules_section"]}
    surfaces = _payload_surfaces(profile)
    for family, obj in surfaces.items():
        python_form = obj.model_dump(mode="python") if hasattr(obj, "model_dump") else obj
        assert _nonfinite_paths(python_form) == [], f"{family}: non-finite value reached an emitted artifact"
        assert _strict_json(python_form), family
    assert any(lim == "invalid_source_contract:MALFORMED_SHAPE" for lim in surfaces["plan"].limitations), "non-conclusive"


# Float sink census: every leaf of a rich modern config is replaced by NaN, one at a time.
_RICH_CONTRACTS = {
    "version": "1",
    "updated": "2026-10-06",
    "system": {"name": "s", "build": {"n": 1}},
    "c1": {
        "description": "d",
        "scope": "global",
        "paths": ["backend/api/*"],
        "patterns": ["backend/*"],
        "rules": [{"rule": "r", "expected_value": 2.5, "invariant": True, "rationale": "why", "field": "f"}, "plain rule"],
        "slot_rules": [{"rule": "sr", "expected_value": 3.5}],
        "extra_section": ["x", "y"],
    },
}
_RICH_PACKS = {
    "version": "1",
    "packs": {"p1": {"description": "pack", "domain_contract": "c1", "recommended_review_preset": "deep", "paths": ["backend/*"], "patterns": ["api"], "scope": "global", "critical": True, "notes": "n"}},
    "contract_bindings": {"p1": ["c1"]},
}


def _leaf_paths(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _leaf_paths(v, (*path, k))]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _leaf_paths(v, (*path, i))]
    return [path]


def _set_at(root: Any, path: tuple[Any, ...], new: Any) -> Any:
    clone = copy.deepcopy(root)
    cursor = clone
    for step in path[:-1]:
        cursor = cursor[step]
    cursor[path[-1]] = new
    return clone


def _surfaces_for(contracts: Any, packs: Any) -> dict[str, Any]:
    intake = _base_intake()
    intake.target_profile = {"domain_contracts": contracts, "review_packs": packs}
    plan = build_semantic_chunk_plan(intake.model_dump(mode="json"), max_blocks=2, max_chars_per_block=15000)
    _, payloads = build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=_brief(intake, plan), checks=None, validation_evidence=None)
    ctx_all = [
        pcm.contracts_context(intake, chunk_files=["backend/api/shifts.py"], chunk_contracts=["target_profile:domain_contracts", "target_profile:review_packs"],
                              chunk_id="c", selected_contract_pack="p1", semantic_group="api_schema_contract")[0]
    ]
    return {"plan": plan.model_dump(mode="python"), "payloads": {k: v.chunk_context for k, v in payloads.items()}, "contexts": ctx_all}


def test_r5c_float_sink_census_every_leaf_replaced_by_nan_never_reaches_an_emitted_artifact() -> None:
    baseline = _surfaces_for(_RICH_CONTRACTS, _RICH_PACKS)
    assert _nonfinite_paths(baseline) == []
    accepted_not_emitted: list[str] = []
    rejected: list[str] = []
    leaves = [("contracts", p) for p in _leaf_paths(_RICH_CONTRACTS)] + [("packs", p) for p in _leaf_paths(_RICH_PACKS)]
    for which, path in leaves:
        contracts = _set_at(_RICH_CONTRACTS, path, float("nan")) if which == "contracts" else _RICH_CONTRACTS
        packs = _set_at(_RICH_PACKS, path, float("nan")) if which == "packs" else _RICH_PACKS
        surfaces = _surfaces_for(contracts, packs)
        assert _nonfinite_paths(surfaces) == [], f"non-finite value emitted for {which}:{'.'.join(map(str, path))}"
        json.dumps(surfaces, allow_nan=False, default=str)  # strict-JSON oracle over the emitted surfaces
        invalid = any(lim.startswith("invalid_source_") for lim in surfaces["plan"]["limitations"])
        (rejected if invalid else accepted_not_emitted).append(f"{which}:{'.'.join(map(str, path))}")
    assert rejected and accepted_not_emitted is not None
    # the only float-bearing emitted field is rule.expected_value; every other float admission is provably non-emitted
    assert all("expected_value" not in a for a in accepted_not_emitted)


def test_ab_r5c_nonfinite_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    """AB-R5C-NONFINITE-ADMISSION: remove the isfinite predicate; the strict-JSON oracle must go RED."""
    profile = {"domain_contracts": _expected_value_docs(float("nan"))["rules_section"]}

    def oracle_green() -> bool:
        surfaces = _payload_surfaces(profile)
        try:
            return all(not _nonfinite_paths(o.model_dump(mode="python") if hasattr(o, "model_dump") else o) for o in surfaces.values())
        except ValueError:
            return False

    assert oracle_green() is True
    with monkeypatch.context() as m:
        m.setattr(pcm, "_is_strict_json_scalar", lambda value: isinstance(value, (str, int, float, bool)))
        assert oracle_green() is False, "mutant must go RED: NaN reaches the admitted payload"
        with pytest.raises(ValueError):
            _strict_json(_payload_surfaces(profile)["chunk_payloads"])
    assert oracle_green() is True
