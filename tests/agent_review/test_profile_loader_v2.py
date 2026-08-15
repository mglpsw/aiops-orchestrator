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
# PR-A: ONE strict semantics for target-authored profile YAML
#
# Duplicate detection runs over the COMPOSED NODE GRAPH, before construction
# and before `flatten_mapping` splices merge sources into their parents.
# Doing it during construction is what made the earlier attempt depth-
# sensitive: PyYAML drains deferred constructors breadth-first, so a
# shallower node could flatten an anchor in place before the anchor's own
# mapping was ever constructed.
# ===========================================================================


def _valid_profile_yaml(**identity: str) -> str:
    import json as _json

    data = _profile_dict()
    if identity:
        data["identity"] = {"repo": identity["repo"], "default_branch": "main"}
    return _json.dumps(data)


_DUPLICATE_KEY_CORPUS = [
    ("plain_duplicate", "identity:\n  repo: a/b\n  default_branch: main\n  repo: attacker/evil\n"),
    ("duplicate_inside_inline_merge_source", "identity:\n  <<: {repo: a/b, repo: attacker/evil}\n"),
    ("duplicate_in_anchored_merge_source", "src: &s {repo: a/b, repo: attacker/evil}\nidentity:\n  <<: *s\n"),
    ("duplicate_at_top_level", "schema_version: 2\nschema_version: 3\n"),
    ("duplicate_nested_in_sequence", "artifacts:\n  - {artifact_id: a, artifact_id: b}\n"),
]


@pytest.mark.parametrize(
    "label,text", _DUPLICATE_KEY_CORPUS, ids=[c[0] for c in _DUPLICATE_KEY_CORPUS]
)
def test_family_duplicate_keys_are_refused_wherever_they_appear(label: str, text: str) -> None:
    """A duplicated key makes the document ambiguous: `yaml.safe_load`
    resolves it last-wins, so the SAME bytes mean different things to a
    first-wins reader or a human auditor. Since this loader also backs the
    writer, an ambiguous profile would mint a receipt and a
    `target_profile_hash` for one of two readings.

    `duplicate_inside_inline_merge_source` is the case a construction-time
    scan misses entirely: the merge key is skipped before the constructor
    ever descends into the source mapping.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


# Positive corpus: every form below is legal YAML that `yaml.safe_load`
# accepts, and NONE of it may be refused. A merge OVERRIDE is not a
# duplicate -- `<<` supplies defaults that the mapping's own key replaces.
_LEGAL_MERGE_CORPUS = [
    ("plain_anchor_no_merge", "a: &x {t: 30}\nb: *x\n"),
    ("simple_merge", "a: &x {t: 30}\nb: {<<: *x}\n"),
    ("merge_override_same_depth", "a: &x {t: 30}\nb: {<<: *x, t: 60}\n"),
    ("nested_anchor_merged_shallower", "base: &b {t: 30}\nouter: {inner: &m {<<: *b, t: 60}}\nd: {<<: *m}\n"),
    ("multiple_merge_sources", "a: &x {p: 1}\nb: &y {q: 2}\nc: {<<: [*x, *y], r: 3}\n"),
    ("merge_chain_with_overrides", "a: &x {t: 1}\nb: &y {<<: *x, t: 2}\nc: {<<: *y, t: 3}\n"),
    ("repeated_alias_same_anchor", "a: &x {t: 1}\nb: *x\nc: *x\nd: {<<: *x}\n"),
]


@pytest.mark.parametrize(
    "label,text", _LEGAL_MERGE_CORPUS, ids=[c[0] for c in _LEGAL_MERGE_CORPUS]
)
def test_family_legal_anchor_and_merge_forms_are_never_refused_as_unreadable(
    label: str, text: str
) -> None:
    """Regression guard for the whole anchor/merge surface.

    These documents are not valid `TargetProfileV2` instances, so they must
    fail at the CONTRACT layer (`invalid`) -- never at the YAML layer
    (`unreadable`). Any duplicate-detection strategy that rejects one of
    these has broken YAML semantics rather than tightened them, and this
    loader is shared by every profile reader AND the writer.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    assert yaml.safe_load(text) is not None, f"{label} must be legal YAML to begin with"
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2, (
        f"{label} was rejected at the YAML layer; legal merge semantics regressed"
    )


def test_a_real_profile_using_merge_overrides_still_loads() -> None:
    """End-to-end positive control: a genuinely VALID profile authored with
    an anchor and a merge override must load, validate, and actually
    inherit the merged fields."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    text = """
schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity: {repo: acme/svc, default_branch: main}
artifacts:
  - &art_defaults
    artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
  - <<: *art_defaults
    artifact_id: second
    path: artifacts/second.diff
budgets:
  max_chunks: 16
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review: {paths: [], patterns: [], artifact_ids: [], minimum_coverage: complete}
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks: [pytest]
  allowed_semantic_groups: [primary_backend_logic]
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""
    profile = load_target_profile_text_v2(text)
    assert [a.artifact_id for a in profile.artifacts] == ["full-diff", "second"]
    # The merge really supplied the inherited fields, and the mapping's own
    # keys really overrode the merged ones.
    assert profile.artifacts[1].max_bytes == 1000000
    assert profile.artifacts[1].path == "artifacts/second.diff"


_MALFORMED_YAML_CORPUS = [
    ("explicit_map_tag_on_sequence", "identity: !!map [1, 2]"),
    ("explicit_map_tag_on_scalar", "identity: !!map foo"),
    ("explicit_set_tag", "identity: !!set [a, b]"),
    ("unhashable_key", "? [a, b]\n: value"),
    ("not_yaml_at_all", "identity: [unclosed"),
    ("empty_document", ""),
    ("bare_scalar_document", "42"),
]


@pytest.mark.parametrize(
    "label,text", _MALFORMED_YAML_CORPUS, ids=[c[0] for c in _MALFORMED_YAML_CORPUS]
)
def test_family_malformed_profile_yaml_is_reason_coded_never_a_raw_exception(
    label: str, text: str
) -> None:
    """Target-authored YAML must never escape as an untyped exception.

    An explicitly tagged non-mapping node is the case that broke a
    construction-time override: it unpacked `node.value` as key/value pairs
    before PyYAML's own `isinstance(node, MappingNode)` guard could run, so
    a raw `TypeError`/`ValueError` escaped past every caller's
    `TargetProfileLoadErrorV2` boundary and reached the CLI as a traceback.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code in {
        TARGET_PROFILE_UNREADABLE_REASON_V2,
        TARGET_PROFILE_INVALID_REASON_V2,
    }, label
