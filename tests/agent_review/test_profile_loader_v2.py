from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.agent_review.profile_loader_v2 import (
    TARGET_PROFILE_INVALID_REASON_V2,
    TARGET_PROFILE_MISSING_REASON_V2,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
    TargetProfileLoadErrorV2,
    _read_unambiguously_v2,
    compute_policy_hash_v2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
    load_target_profile_v2,
)
from tests.agent_review.target_profile_yaml_corpus import case as corpus_case
from tests.agent_review.target_profile_yaml_corpus import cases as corpus_cases


def _profile_dict(**overrides: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "schema_id": "agent-review.target-profile.v2",
        "schema_version": 2,
        "source": "repo-profile",
        "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
        "artifacts": [
            {
                "artifact_id": "full-diff",
                "path": "artifacts/full.diff",
                "kind": "diff",
                "required": True,
                "max_bytes": 1000000,
            }
        ],
        "budgets": {
            "max_chunks": 32,
            "total_prompt_chars": 250000,
            "max_chars_per_chunk": 24000,
            "max_files_per_chunk": 50,
            "max_contracts_per_chunk": 50,
        },
        "must_review": {
            "paths": ["app/service.py"],
            "patterns": ["app/**/*.py"],
            "artifact_ids": ["full-diff"],
            "minimum_coverage": "complete",
        },
        "policies": {
            "network_policy": "forbidden",
            "fail_closed": True,
            "redaction_required": True,
            "allow_partial_coverage": False,
            "required_checks": ["pytest"],
            "allowed_semantic_groups": ["api_schema_contract", "tests"],
            "coverage_failure_state": "blocked_pipeline",
            "model_uncertainty_state": "manual_required",
        },
        "contracts": [
            {
                "contract_id": "contract.api",
                "contract_version": "1",
                "path": ".aiops/domain-contracts.yaml",
                "sha256": "f" * 64,
                "scope": "repository",
                "required": True,
            }
        ],
        "limitations": [],
    }
    profile.update(overrides)
    return profile


def _write_profile(repo_root: Path, data: object) -> None:
    aiops_dir = repo_root / ".aiops"
    aiops_dir.mkdir(parents=True, exist_ok=True)
    (aiops_dir / "target-profile.v2.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_load_target_profile_v2_accepts_a_strict_valid_profile(tmp_path: Path) -> None:
    _write_profile(tmp_path, _profile_dict())
    profile = load_target_profile_v2(tmp_path)
    assert profile.identity.repo == "mglpsw/aiops-orchestrator"


def test_load_target_profile_v2_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_MISSING_REASON_V2


def test_load_target_profile_v2_rejects_unreadable_yaml(tmp_path: Path) -> None:
    aiops_dir = tmp_path / ".aiops"
    aiops_dir.mkdir(parents=True)
    (aiops_dir / "target-profile.v2.yaml").write_text(
        "not: [valid: yaml: at: all", encoding="utf-8"
    )
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2


def test_load_target_profile_v2_rejects_a_non_mapping_document(tmp_path: Path) -> None:
    aiops_dir = tmp_path / ".aiops"
    aiops_dir.mkdir(parents=True)
    (aiops_dir / "target-profile.v2.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2


def test_load_target_profile_v2_rejects_an_unknown_field(tmp_path: Path) -> None:
    _write_profile(tmp_path, _profile_dict(unexpected_field="nope"))
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_a_missing_required_field(tmp_path: Path) -> None:
    data = _profile_dict()
    del data["policies"]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


@pytest.mark.parametrize(
    "field_path,value",
    [
        (("policies", "network_policy"), "allowed"),
        (("policies", "fail_closed"), False),
        (("policies", "redaction_required"), False),
        (("policies", "allow_partial_coverage"), True),
    ],
)
def test_load_target_profile_v2_rejects_a_target_weakening_a_hard_boundary(
    tmp_path: Path, field_path: tuple[str, str], value: object
) -> None:
    data = _profile_dict()
    data["policies"][field_path[1]] = value  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


@pytest.mark.parametrize(
    "field,value",
    [
        ("required", "true"),  # string instead of bool
        ("max_bytes", "1000000"),  # string instead of int
    ],
)
def test_load_target_profile_v2_rejects_silent_type_coercion(
    tmp_path: Path, field: str, value: object
) -> None:
    data = _profile_dict()
    data["artifacts"][0][field] = value  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_an_absolute_artifact_path(tmp_path: Path) -> None:
    data = _profile_dict()
    data["artifacts"][0]["path"] = "/etc/passwd"  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_a_traversal_artifact_path(tmp_path: Path) -> None:
    data = _profile_dict()
    data["artifacts"][0]["path"] = "../../etc/passwd"  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_a_windows_style_artifact_path(tmp_path: Path) -> None:
    data = _profile_dict()
    data["artifacts"][0]["path"] = "artifacts\\full.diff"  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_a_glob_in_a_concrete_must_review_path(
    tmp_path: Path,
) -> None:
    data = _profile_dict()
    data["must_review"]["paths"] = ["app/*.py"]  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_load_target_profile_v2_rejects_empty_required_checks(tmp_path: Path) -> None:
    data = _profile_dict()
    data["policies"]["required_checks"] = []  # type: ignore[index]
    _write_profile(tmp_path, data)
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(tmp_path)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


# -- profile/policy hash reproducibility -----------------------------------


def test_profile_hash_is_deterministic_and_material_changes_flip_it(tmp_path: Path) -> None:
    _write_profile(tmp_path, _profile_dict())
    profile_a = load_target_profile_v2(tmp_path)
    profile_b = load_target_profile_v2(tmp_path)
    assert compute_profile_hash_v2(profile_a) == compute_profile_hash_v2(profile_b)

    other = _profile_dict()
    other["limitations"] = ["something-new"]
    tmp_path_2 = tmp_path / "other"
    _write_profile(tmp_path_2, other)
    profile_c = load_target_profile_v2(tmp_path_2)
    assert compute_profile_hash_v2(profile_c) != compute_profile_hash_v2(profile_a)


def test_policy_hash_changes_independently_of_unrelated_profile_fields(tmp_path: Path) -> None:
    _write_profile(tmp_path, _profile_dict())
    baseline = load_target_profile_v2(tmp_path)

    same_policy_different_limitations = _profile_dict()
    same_policy_different_limitations["limitations"] = ["unrelated-note"]
    other_root = tmp_path / "same-policy"
    _write_profile(other_root, same_policy_different_limitations)
    same_policy = load_target_profile_v2(other_root)

    assert compute_profile_hash_v2(same_policy) != compute_profile_hash_v2(baseline)
    assert compute_policy_hash_v2(same_policy) == compute_policy_hash_v2(baseline)

    different_policy = _profile_dict()
    different_policy["policies"]["required_checks"] = ["pytest", "mypy"]  # type: ignore[index]
    different_root = tmp_path / "different-policy"
    _write_profile(different_root, different_policy)
    changed_policy = load_target_profile_v2(different_root)
    assert compute_policy_hash_v2(changed_policy) != compute_policy_hash_v2(baseline)


# ===========================================================================
# Target-profile YAML ambiguity authority.
#
# Ambiguity is DERIVED from the parser, not re-derived from its rules: the
# authority observes the exact point the real constructor is about to
# resolve a collision between two entries the document authored, and
# refuses before the resolution happens. It does not compare two readings
# and it does not canonicalise or project to JSON to decide anything --
# see `docs/adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md` for
# the normative decision and `docs/engineering/
# AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md` for why the earlier
# comparison-based design was superseded.
#
# The corpus itself lives in `tests/agent_review/fixtures/
# target_profile_yaml/` (`CORPUS.json` + one `.yamlcase` file per case),
# not inline here -- it is the accumulated adversarial and safe
# counterexample corpus of PR #236's seven review rounds plus PR #237's
# reproducers, with every case's disposition and exact reason code
# observed against this module, never guessed. It is systematic evidence
# over the families it enumerates, not a completeness proof over PyYAML.
# ===========================================================================


@pytest.mark.parametrize(
    "case", corpus_cases("ambiguous"), ids=[c.case_id for c in corpus_cases("ambiguous")]
)
def test_family_ambiguous_documents_are_refused(case) -> None:
    """A collision the real constructor would have to resolve silently is
    refused instead, so no receipt or `target_profile_hash` may be minted
    from either candidate reading."""
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(case.text)
    assert excinfo.value.reason_code == case.expected_reason_code, case.case_id


@pytest.mark.parametrize(
    "case", corpus_cases("legal"), ids=[c.case_id for c in corpus_cases("legal")]
)
def test_family_legal_documents_read_exactly_as_stock_safeloader(case) -> None:
    """The authority must agree with stock `yaml.safe_load` on every legal
    document.

    Asserted as EQUALITY, not as "does not raise". The previous design's
    worst failures were over-refusals -- documents stock YAML accepts,
    rejected because a re-derived rule mismodelled the parser -- and only
    an equality assertion catches that direction.

    `legal` is a YAML-authority reading-level property (parity with stock
    `safe_load`), not a claim that the fragment is a contractually valid
    `TargetProfileV2` -- most of these documents are deliberately not.
    """
    assert _read_unambiguously_v2(case.text) == yaml.safe_load(case.text), case.case_id


@pytest.mark.parametrize(
    "case", corpus_cases("invalid"), ids=[c.case_id for c in corpus_cases("invalid")]
)
def test_family_invalid_documents_are_refused_by_language_or_contract(case) -> None:
    """Legal, unambiguous YAML that the profile language or contract
    refuses anyway: merge keys (`<<:` is not part of the language
    `TargetProfileV2` accepts, matching `authoritative_check_policy_v2`,
    which has never supported it), a document whose only collision is
    manufactured by an intermediate JSON round-trip that no longer exists,
    and a document that reads without ambiguity but fails contract
    validation on an unknown field."""
    assert yaml.safe_load(case.text) is not None, f"{case.case_id}: precondition is legal YAML"
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(case.text)
    assert excinfo.value.reason_code == case.expected_reason_code, case.case_id


@pytest.mark.parametrize(
    "case", corpus_cases("malformed"), ids=[c.case_id for c in corpus_cases("malformed")]
)
def test_family_malformed_input_is_reason_coded_never_raw(case) -> None:
    """Target-authored YAML never escapes as an untyped exception, and the
    exact reason code is asserted -- not membership in the two-valued
    reason-code set, which would prove only that the error is typed."""
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(case.text)
    assert excinfo.value.reason_code == case.expected_reason_code, case.case_id


def test_the_file_layer_normalises_invalid_utf8(tmp_path: Path) -> None:
    """The boundary covers every layer that touches target-authored bytes,
    including the read: invalid UTF-8 fails in `read_text`'s DECODE step,
    a `ValueError`, not an `OSError`."""
    root = tmp_path / "target"
    (root / ".aiops").mkdir(parents=True)
    (root / ".aiops" / "target-profile.v2.yaml").write_bytes(b"identity: \xff\xfe not utf8\n")

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(root)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2


def test_the_shipped_seed_template_still_loads() -> None:
    """End-to-end control: the template this pack installs must survive the
    authority."""
    seed = Path(__file__).resolve().parents[2] / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    assert load_target_profile_text_v2(seed.read_text(encoding="utf-8")).identity.repo


def test_no_intermediate_step_manufactures_a_duplicate_key_interpretation() -> None:
    """Self-discovered during the round-7 verdict, and fixed by removing
    the step rather than by adding a rule.

    `{"1": a, 1: b}` (corpus case `manufactured_json_duplicate`, held once,
    in the corpus, not as a second literal copy here) has two DISTINCT
    Python keys and no collision, so the authority accepts it. The
    previous validation step then ran `json.dumps`, which coerces
    non-string keys to strings, producing the literal duplicate-key
    document `{"1": "a", "1": "b"}` and reparsing it last-wins -- a second
    key-resolution policy introduced downstream of the authority whose
    entire purpose is refusing one.

    Contract validation is now direct, so no intermediate step can
    manufacture an interpretation. The document is still refused, but by
    the CONTRACT (unknown fields), which is the layer that owns that
    decision.
    """
    text = corpus_case("manufactured_json_duplicate").text
    parsed = _read_unambiguously_v2(text)
    assert parsed == yaml.safe_load(text), "the authority must not alter a collision-free document"
    assert set(parsed["identity"]) == {"1", 1}, "both distinct keys survive; nothing was collapsed"

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


def test_contract_validation_introduces_no_key_resolution_policy() -> None:
    """Direct validation and the old JSON round-trip agree on every valid
    profile, so removing the round-trip changes nothing except the ability
    to manufacture duplicates. Measured, not assumed."""
    import json as _json

    from pydantic import ValidationError

    from app.agent_review.contracts_v2 import TargetProfileV2

    seed = Path(__file__).resolve().parents[2] / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    base = yaml.safe_load(seed.read_text(encoding="utf-8"))
    variants = [base]
    for mutate in (
        lambda d: d.__setitem__("limitations", ["note-one"]),
        lambda d: d["policies"].__setitem__("required_checks", ["pytest", "mypy"]),
        lambda d: d["budgets"].__setitem__("max_chunks", 8),
        lambda d: d["must_review"].__setitem__("paths", ["app/x.py"]),
    ):
        variant = _json.loads(_json.dumps(base))
        mutate(variant)
        variants.append(variant)

    for variant in variants:
        direct = TargetProfileV2.model_validate(variant)
        via_json = TargetProfileV2.model_validate_json(_json.dumps(variant, ensure_ascii=False), strict=True)
        assert direct == via_json


def test_the_validated_object_is_the_parsed_object_not_a_reserialisation(monkeypatch) -> None:
    """Mechanism test, deliberately, because the BEHAVIOUR is identical.

    Restoring the JSON round-trip does not change any outcome in the
    corpus: `{"1": a, 1: b}` is refused either way -- by the contract as an
    unknown field, or by the round-trip's manufactured duplicate. The first
    version of this guard asserted the outcome and therefore did not
    discriminate the mutation at all.

    What actually differs is whether validation re-serialises. A
    re-serialisation is a second key-resolution policy applied downstream
    of the authority that exists to refuse one, so the property is "the
    object validated is the object parsed" -- and that can only be
    observed at the seam.
    """
    import app.agent_review.profile_loader_v2 as module

    calls: list[object] = []
    real_dumps = module.json.dumps

    def spy(*args, **kwargs):
        calls.append(args[0] if args else None)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(module.json, "dumps", spy)

    profile_text = (Path(__file__).resolve().parents[2] / "templates"
                    / "agentreview-v2-target-pack" / "target-profile.v2.yaml").read_text(encoding="utf-8")
    module.load_target_profile_text_v2(profile_text)

    parsed = module._read_unambiguously_v2(profile_text)
    assert parsed not in calls, (
        "the parsed profile was re-serialised during validation; contract "
        "validation must not introduce a second key-resolution policy"
    )
