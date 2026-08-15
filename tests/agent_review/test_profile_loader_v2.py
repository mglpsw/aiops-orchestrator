from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.agent_review.profile_loader_v2 import (
    TARGET_PROFILE_INVALID_REASON_V2,
    TARGET_PROFILE_MISSING_REASON_V2,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
    TargetProfileLoadErrorV2,
    compute_policy_hash_v2,
    compute_profile_hash_v2,
    load_target_profile_v2,
)


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
# same bytes are read under both duplicate-resolution policies, and a
# document is refused when the two conforming readings disagree.
#
# The corpus below is the accumulated adversarial corpus of PR #236's seven
# review rounds. Its point is that NONE of these classes needs a rule of
# its own any more -- no scalar/mapping-key distinction, no `!!value` rule,
# no JSON-projection rule, no constructed-key hash table, no merge
# cardinality machinery. They are consequences of measuring what the parser
# consumes.
#
# The corpus is systematic evidence over the families it enumerates. It is
# not a completeness proof over PyYAML.
# ===========================================================================

_AMBIGUOUS_CORPUS = [
    ("plain_duplicate_divergent", "identity:\n  repo: a/b\n  repo: attacker/evil\n"),
    ("quoted_vs_plain", 'identity:\n  repo: a/b\n  "repo": attacker/evil\n'),
    ("collapse_yes_true", "identity:\n  yes: 1\n  true: 2\n"),
    ("collapse_int_float", "identity:\n  1: a\n  1.0: b\n"),
    ("collapse_hex_dec", "identity:\n  0x10: a\n  16: b\n"),
    ("collapse_tilde_null", "identity:\n  ~: a\n  null: b\n"),
    ("collapse_bool_spellings", "identity:\n  on: 1\n  On: 2\n  TRUE: 3\n"),
    ("tagged_str_duplicate_value_key", "m:\n  ? !!str {=: repo, =: default_branch}\n  : v\n"),
    ("tagged_str_same_tag_distinct", "m:\n  ? !!str {!!value a: x, !!value b: y}\n  : v\n"),
    ("contextual_nested_value", "m:\n  ? !!str {=: {!!value left: repo, !!value right: db}}\n  : v\n"),
    ("duplicate_inside_key_mapping", "m:\n  ? !!str {a: {d: 1, d: 2}}\n  : v\n"),
    ("duplicate_nested_in_sequence", "artifacts:\n  - {artifact_id: a, artifact_id: b}\n"),
]


@pytest.mark.parametrize("label,text", _AMBIGUOUS_CORPUS, ids=[c[0] for c in _AMBIGUOUS_CORPUS])
def test_family_ambiguous_documents_are_refused(label: str, text: str) -> None:
    """Two conforming readings of these bytes disagree, so no receipt or
    `target_profile_hash` may be minted from either."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


_SAFE_CORPUS = [
    ("plain_profile_shape", "identity: {repo: owner/repo, default_branch: main}\n"),
    ("duplicate_keys_same_result", "identity:\n  repo: a/b\n  repo: a/b\n"),
    ("nested_anchors", "a: &x {t: 1}\nb: {c: *x}\nd: *x\n"),
    ("aliases_repeated", "a: &x {t: 1}\nb: *x\nc: *x\n"),
    ("explicit_scalar_tags", "a: !!int 7\nb: !!float 1.5\nc: !!bool yes\n"),
    ("tagged_str_map_key", "m:\n  ? !!str {=: repo}\n  : v\n"),
    # Round 7 safe counterexamples -- the walker began refusing all three.
    ("legal_value_key_retagged", "identity: {!!value repo: owner/repo, default_branch: main}\n"),
    ("discarded_plain_siblings", "m:\n  ? !!str {=: identity, left: one, right: two}\n  : v\n"),
    ("unconsumed_int_sibling", "m:\n  ? !!str {=: repo, 123: ignored}\n  : v\n"),
    ("block_scalars", "a: |\n  l1\n  l2\nb: >\n  folded\n"),
    ("empty_containers", "a: {}\nb: []\n"),
    ("sequence_of_mappings", "a:\n  - {x: 1}\n  - {y: 2}\n"),
]


@pytest.mark.parametrize("label,text", _SAFE_CORPUS, ids=[c[0] for c in _SAFE_CORPUS])
def test_family_legal_documents_read_exactly_as_stock_safeloader(label: str, text: str) -> None:
    """The authority must agree with stock `yaml.safe_load` on every legal
    document.

    Asserted as EQUALITY, not as "does not raise". The previous design's
    worst failures were over-refusals -- documents stock YAML accepts,
    rejected because a re-derived rule mismodelled the parser -- and only
    an equality assertion catches that direction."""
    from app.agent_review.profile_loader_v2 import _read_unambiguously_v2

    assert _read_unambiguously_v2(text) == yaml.safe_load(text), label


_MERGE_CORPUS = [
    ("simple_merge", "a: &x {t: 1}\nb: {<<: *x}\n"),
    ("merge_override", "a: &x {t: 1}\nb: {<<: *x, t: 2}\n"),
    ("merge_sequence", "a: &x {p: 1}\nb: &y {q: 2}\nc: {<<: [*x, *y], r: 3}\n"),
    ("merge_chain", "a: &x {t: 1}\nb: &y {<<: *x, t: 2}\nc: {<<: *y, t: 3}\n"),
    ("duplicate_merge_keys", "a: &x {k: 1}\nb: &y {k: 2}\nc:\n  <<: *x\n  <<: *y\n"),
    ("inline_merge_source", "identity:\n  <<: {repo: a/b, repo: evil}\n"),
    ("nested_merge_shallower", "base: &b {t: 30}\nouter: {inner: &m {<<: *b, t: 60}}\nd: {<<: *m}\n"),
]


@pytest.mark.parametrize("label,text", _MERGE_CORPUS, ids=[c[0] for c in _MERGE_CORPUS])
def test_family_merge_keys_are_not_part_of_the_profile_language(label: str, text: str) -> None:
    """`<<:` is unsupported, matching `authoritative_check_policy_v2`,
    which has never supported it.

    This is a language decision, taken deliberately. YAML's merge spec
    already defines which entry wins, so a duplicate-resolution policy
    cannot be varied independently of it without re-deriving merge
    provenance -- which is exactly the re-derivation this design exists to
    remove. Refusing merge keeps the authority a measurement rather than a
    reimplementation."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    assert yaml.safe_load(text) is not None, "precondition: legal YAML"
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2, label


_MALFORMED_CORPUS = [
    ("bad_int_tag", "x: !!int nope\n"),
    ("empty_int_tag", "x: !!int \n"),
    ("bad_bool_tag", "x: !!bool nope\n"),
    ("bad_timestamp_tag", "x: !!timestamp nope\n"),
    ("mapping_shaped_timestamp", "x: !!timestamp {=: 2020-01-01}\n"),
    ("integer_past_digit_limit", "x: " + "9" * 5000 + "\n"),
    ("unhashable_key", "? [a, b]\n: value\n"),
    ("recursive_alias_value", "a: &x {self: *x}\n"),
    ("recursive_alias_key", "? &x {self: *x}\n: v\n"),
    ("not_yaml", "identity: [unclosed\n"),
    ("empty_document", ""),
    ("bare_scalar", "42\n"),
    ("nul_character", "x: a\x00b\n"),
    ("bel_character", "x: a\x07b\n"),
    ("lone_surrogate", "x: a\ud800b\n"),
]


@pytest.mark.parametrize("label,text", _MALFORMED_CORPUS, ids=[c[0] for c in _MALFORMED_CORPUS])
def test_family_malformed_input_is_reason_coded_never_raw(label: str, text: str) -> None:
    """Target-authored YAML never escapes as an untyped exception."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code in {
        TARGET_PROFILE_UNREADABLE_REASON_V2,
        TARGET_PROFILE_INVALID_REASON_V2,
    }, label


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
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    seed = Path(__file__).resolve().parents[2] / "templates" / "agentreview-v2-target-pack" / "target-profile.v2.yaml"
    assert load_target_profile_text_v2(seed.read_text(encoding="utf-8")).identity.repo
