from __future__ import annotations

import sys
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


# AMBIGUITY, with STRING keys -- so this family exercises duplicate
# detection itself, not the string-key domain rule that now precedes it.
# Every entry means two things to two conforming readers.
_DUPLICATE_KEY_CORPUS = [
    ("plain_duplicate", "identity:\n  repo: a/b\n  default_branch: main\n  repo: attacker/evil\n"),
    ("quoted_vs_plain", 'identity:\n  repo: a/b\n  "repo": attacker/evil\n'),
    ("single_vs_double_quoted", "identity:\n  'repo': a/b\n  \"repo\": attacker/evil\n"),
    ("duplicate_inside_inline_merge_source", "identity:\n  <<: {repo: a/b, repo: attacker/evil}\n"),
    ("duplicate_in_anchored_merge_source", "src: &s {repo: a/b, repo: attacker/evil}\nidentity:\n  <<: *s\n"),
    ("duplicate_at_top_level", "schema_id: a\nschema_id: b\n"),
    ("duplicate_nested_in_sequence", "artifacts:\n  - {artifact_id: a, artifact_id: b}\n"),
]


@pytest.mark.parametrize(
    "label,text", _DUPLICATE_KEY_CORPUS, ids=[c[0] for c in _DUPLICATE_KEY_CORPUS]
)
def test_family_duplicate_keys_are_refused_wherever_they_appear(label: str, text: str) -> None:
    """A duplicated key makes the document mean two different things to two
    conforming readers -- `yaml.safe_load` keeps the last, a first-wins
    reader or a human auditor sees the first. Because this loader also
    backs the install WRITER, an ambiguous profile would mint a receipt and
    a `target_profile_hash` for one reading while the other stays equally
    defensible."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


# NON-STRING constructed keys: outside TargetProfileV2's domain entirely.
# Refused as contract-invalid BEFORE reaching the duplicate hash table --
# which is also what removes the integer-hash-collision attack, since a
# colliding key is refused on sight rather than inserted.
_NON_STRING_KEY_CORPUS = [
    ("int_key", "identity:\n  1: a\n"),
    ("float_key", "identity:\n  1.5: a\n"),
    ("bool_key", "identity:\n  yes: a\n"),
    ("null_key", "identity:\n  ~: a\n"),
    ("hex_int_key", "identity:\n  0x10: a\n"),
    # The collapse pairs from earlier rounds: still refused, now by domain
    # rather than by ambiguity, and refused EARLIER.
    ("collapse_yes_true", "identity:\n  yes: 1\n  true: 2\n"),
    ("collapse_int_float", "identity:\n  1: a\n  1.0: b\n"),
    ("collapse_bool_spellings", "identity:\n  on: 1\n  On: 2\n  TRUE: 3\n"),
    ("projection_str_vs_int", 'identity:\n  "1": a\n  1: b\n'),
    ("projection_str_vs_bool", 'identity:\n  "true": a\n  true: b\n'),
]


@pytest.mark.parametrize(
    "label,text", _NON_STRING_KEY_CORPUS, ids=[c[0] for c in _NON_STRING_KEY_CORPUS]
)
def test_family_non_string_constructed_keys_are_refused_as_contract_invalid(
    label: str, text: str
) -> None:
    """`TargetProfileV2` is JSON-shaped: strict models, `extra="forbid"`,
    named fields, and canonical JSON has no non-string object keys. A key
    constructing to int/float/bool/None is already outside that domain, so
    refusing it is EARLY enforcement of a property the contract has -- not
    a new restriction on the language.

    Deliberately not a key-count ceiling: a cap would be a genuinely new
    property ("N+1 fields refused by quantity") the contract does not have.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2, label


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


def test_recursive_alias_terminates_deterministically() -> None:
    """A self-referential anchor must not hang the scan.

    `visited` is keyed on node identity precisely so the composed graph's
    cycle is walked once. Termination is asserted here rather than left to
    the incidental `json.dumps` circular-reference guard downstream, which
    a later refactor of the validation round-trip could remove."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2):
        load_target_profile_text_v2("identity: &x {self: *x}\n")


def test_the_validation_round_trip_cannot_manufacture_a_duplicate_key_document() -> None:
    """Pins the class, and the reason it is now closed one layer earlier.

    `json.dumps` coerces non-string mapping keys to strings, so a profile
    with `{"1": a, 1: b}` -- two legitimately distinct constructed keys --
    used to be re-serialised as the literal duplicate-key document
    `{"identity": {"1": "a", "1": "b"}}` and handed to a last-wins parser.
    An authority whose contract is refusing last-wins ambiguity must not
    produce an ambiguous document itself.

    The offending key is non-string, so it is now refused as
    contract-invalid before construction completes. The separate
    JSON-projection check was removed as SUBSUMED rather than kept as
    unreachable code -- for a string key, projection is the identity
    function, so a projection collision is just a constructed-key
    collision. This test is what would notice if the string-key rule were
    ever relaxed without restoring that check.
    """
    import json as _json

    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        _parse_unambiguous_yaml_v2('identity:\n  "1": a\n  1: b\n')
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2

    # Control: had it been accepted, this is the document that would have
    # been produced -- json.dumps really does collapse the two keys.
    assert _json.dumps({"1": "a", 1: "b"}).count('"1"') == 2


_SCALAR_CONSTRUCTOR_FAILURE_CORPUS = [
    ("bad_int_tag", "identity: {repo: !!int nope}"),
    ("bad_float_tag", "identity: {repo: !!float nope}"),
    ("out_of_range_timestamp", "identity: {repo: !!timestamp 9999-99-99}"),
    ("integer_past_digit_limit", "identity: {repo: " + "9" * 5000 + "}"),
]


@pytest.mark.parametrize(
    "label,text",
    _SCALAR_CONSTRUCTOR_FAILURE_CORPUS,
    ids=[c[0] for c in _SCALAR_CONSTRUCTOR_FAILURE_CORPUS],
)
def test_family_scalar_constructor_failures_are_reason_coded(label: str, text: str) -> None:
    """PyYAML's scalar constructors raise BARE `ValueError`, not `YAMLError`.

    Four target-triggerable inputs escaped the typed contract as
    tracebacks while the earlier totality corpus passed, because that
    corpus only exercised structural malformation. The boundary is a
    property of the exception SET, so it is asserted over a corpus that
    reaches a different PyYAML code path."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


def test_a_typed_refusal_is_never_relabelled_by_its_own_boundary() -> None:
    """`TargetProfileLoadErrorV2` IS a `ValueError`, and the boundary now
    catches `ValueError`. Raising the refusal inside the guarded block
    would let the handler swallow and re-wrap it -- losing nothing today,
    but turning any future distinct reason code into `unreadable`. The
    refusal is therefore raised outside the block, and this pins it."""
    from app.agent_review.profile_loader_v2 import (
        TARGET_PROFILE_UNREADABLE_REASON_V2 as UNREADABLE,
        _parse_unambiguous_yaml_v2,
    )

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        _parse_unambiguous_yaml_v2("identity:\n  repo: a\n  repo: b\n")
    assert excinfo.value.reason_code == UNREADABLE
    # A relabelled refusal would carry the original as __cause__.
    assert not isinstance(excinfo.value.__cause__, TargetProfileLoadErrorV2)


_MERGE_CARDINALITY_CORPUS = [
    ("two_merge_keys_complementary", "r: &r {repo: a/b}\nd: &d {default_branch: main}\nidentity:\n  <<: *r\n  <<: *d\n"),
    ("two_merge_keys_overlapping", "a: &a {k: 1}\nb: &b {k: 2}\nc:\n  <<: *a\n  <<: *b\n"),
    ("three_merge_keys", "a: &a {p: 1}\nb: &b {q: 2}\nc: &c {r: 3}\nd:\n  <<: *a\n  <<: *b\n  <<: *c\n"),
]


@pytest.mark.parametrize(
    "label,text", _MERGE_CARDINALITY_CORPUS, ids=[c[0] for c in _MERGE_CARDINALITY_CORPUS]
)
def test_family_a_mapping_may_author_at_most_one_merge_key(label: str, text: str) -> None:
    """Two authored `<<` keys are ambiguous across conforming readers.

    YAML 1.1 permits ONE merge key whose value may be a sequence of
    sources. PyYAML tolerates several and resolves them differently from
    the sequence spelling -- `{<<: [*a, *b]}` takes the FIRST source's
    value, `{<<: *a, <<: *b}` takes the LAST declaration's. Same intent,
    two spellings, opposite results: exactly the same-bytes ambiguity the
    authority refuses everywhere else.

    Skipping merge keys unconditionally -- correct for "a merge key is not
    an authored key" -- silently also skipped counting them."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


def test_the_sequence_spelling_of_multiple_merge_sources_stays_legal() -> None:
    """Negative control: ONE `<<` whose value is a sequence is the legal,
    unambiguous way to merge several sources, and must not be refused."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    text = "a: &a {p: 1}\nb: &b {q: 2}\nc: {<<: [*a, *b], r: 3}\n"
    assert yaml.safe_load(text)["c"] == {"p": 1, "q": 2, "r": 3}
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2, (
        "the sequence spelling was rejected at the YAML layer; legal merge semantics regressed"
    )


# Every tag YAML 1.1 defines, plus payloads chosen to break each
# constructor's own internals (empty text it indexes, text absent from its
# lookup table, text its regex will not match, a number past the digit
# limit). NOT a list of "inputs someone reported".
_STANDARD_YAML_TAGS = [
    "null", "bool", "int", "float", "binary", "timestamp",
    "str", "seq", "map", "set", "omap", "pairs", "value",
]
_MALFORMED_SCALAR_PAYLOADS = [
    "", "nope", "[1,2]", "{a: b}", "9" * 5000, "9999-99-99", "!!!", "0x", "-", "@@@@", "AAAA====",
    # Forbidden CHARACTERS, not just malformed values. These make
    # `yaml.SafeLoader(text)` raise while the loader object is still being
    # constructed -- so they probe statements the earlier corpus never
    # reached, because it only exercised documents that got as far as
    # having a parser at all.
    "a\x00b", "a\x07b", "a\ud800b", "\x1b[31m",
]


def test_the_parse_boundary_is_total_over_the_whole_tag_space() -> None:
    """Systematic evidence for the boundary -- NOT a completeness proof.

    A finite corpus covers the families enumerated below and the mutations
    demonstrate it discriminates them. It does not demonstrate every
    possible PyYAML behaviour, and the name of this test should not be read
    as claiming that.

    Four exception types were added to this boundary one at a time, each
    after it was reported, while the corpus that claimed to prove totality
    only exercised STRUCTURAL malformation -- bad tags on non-mapping
    nodes, unhashable keys, truncated documents -- and never reached
    PyYAML's scalar constructors at all. `KeyError`, `IndexError` and
    `AttributeError` were found by fuzzing this space, not by report.

    A new PyYAML version, or a tag this repository has never used, is
    caught here instead of by the next reviewer.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    escaped: dict[str, str] = {}
    for tag in _STANDARD_YAML_TAGS:
        for payload in _MALFORMED_SCALAR_PAYLOADS:
            document = f"x: !!{tag} {payload}\n" if payload else f"x: !!{tag}\n"
            try:
                load_target_profile_text_v2(document)
            except TargetProfileLoadErrorV2:
                continue
            except Exception as exc:  # noqa: BLE001 -- the assertion IS that this is unreachable
                escaped[f"!!{tag} {payload[:16]}"] = type(exc).__name__
    assert not escaped, f"target-authored YAML escaped the typed contract: {escaped}"


def test_the_boundary_covers_the_FILE_layer_not_only_the_text_layer(tmp_path: Path) -> None:
    """The typed contract must hold for every layer that touches
    target-authored bytes, not only the one that parses them.

    The tag/character fuzz guard runs against `load_target_profile_text_v2`
    and is therefore structurally blind to the file-reading wrapper above
    it. A profile holding invalid UTF-8 fails in `read_text`'s DECODE
    step -- a `ValueError`, not an `OSError` -- and escaped the contract
    entirely. Same class as the loader-construction finding: right
    boundary, wrong layer.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_v2

    root = tmp_path / "target"
    (root / ".aiops").mkdir(parents=True)
    (root / ".aiops" / "target-profile.v2.yaml").write_bytes(b"identity: \xff\xfe not utf8\n")

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_v2(root)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2


def test_a_non_scalar_key_that_constructs_to_a_string_is_still_a_duplicate() -> None:
    """Comparability is decided by the CONSTRUCTED value, never the node.

    The scan used to skip any non-`ScalarNode` key, on the assumption that
    a complex key is unhashable and PyYAML would refuse it anyway. An
    explicitly tagged node constructs to whatever its tag says, so
    `? !!str {=: repo}` yields the ordinary hashable string `repo` -- and
    the collision with a plain `repo:` was skipped on sight of the node
    type."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    text = "identity:\n  ? !!str {=: repo}\n  : attacker/evil\n  repo: acme/svc\n"
    assert yaml.safe_load(text)["identity"] == {"repo": "acme/svc"}, "one key survives construction"

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2


def test_a_genuinely_unhashable_key_is_still_refused_by_the_loader() -> None:
    """Negative control for the same change: constructing every key must
    not turn an unhashable key into a crash. It stays PyYAML's own typed
    refusal."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2):
        load_target_profile_text_v2("? [a, b]\n: value\n")


def test_duplicate_scanning_is_not_quadratic_in_the_number_of_keys() -> None:
    """Target-authored input must not control superlinear work.

    The scan runs BEFORE schema validation can reject extra fields, so a
    large mapping of unique keys was scanned with LINEAR membership per
    key.

    Asserted as a growth RATIO, and deliberately at sizes where the
    quadratic term dominates. A first version of this test used 400->3200
    keys and did NOT discriminate: at that scale PyYAML's own O(n) parse
    cost swamps the membership cost, and the defective implementation
    passed. Measured separation at these sizes is 8.0x (set) versus 28.7x
    (list), so the threshold below fails on the CURVE rather than on a
    slow runner.
    """
    import time

    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    def elapsed(n: int) -> float:
        document = "identity:\n" + "".join(f"  k{i}: v\n" for i in range(n))
        start = time.perf_counter()
        try:
            _parse_unambiguous_yaml_v2(document)
        except TargetProfileLoadErrorV2:
            pass
        return time.perf_counter() - start

    elapsed(500)  # warm up interpreter/caches
    small, large = elapsed(2500), elapsed(20000)
    ratio = large / small
    assert ratio < 15, f"scan grew {ratio:.1f}x for 8x keys; linear membership suspected"


_KEY_INTERNAL_AMBIGUITY_CORPUS = [
    ("duplicate_inside_mapping_key", "m:\n  ? !!str {=: repo, =: default_branch}\n  : v\n"),
    ("duplicate_inside_nested_mapping_key", "m:\n  ? !!str {a: {d: 1, d: 2}}\n  : v\n"),
    ("duplicate_inside_sequence_key", "m:\n  ? !!str [{d: 1, d: 2}]\n  : v\n"),
]


@pytest.mark.parametrize(
    "label,text",
    _KEY_INTERNAL_AMBIGUITY_CORPUS,
    ids=[c[0] for c in _KEY_INTERNAL_AMBIGUITY_CORPUS],
)
def test_family_ambiguity_inside_a_key_node_is_refused(label: str, text: str) -> None:
    """The walk must reach every authored mapping, wherever it sits.

    A key may itself be a mapping or a sequence carrying its own pairs.
    The walk enqueued only each pair's VALUE, so a duplicate authored
    INSIDE a key -- `? !!str {=: repo, =: default_branch}` -- was never
    scanned at all.

    This is the same class one level further in: round 4 fixed WHICH keys
    get compared, by constructed value rather than node shape; this fixes
    what lives INSIDE a key.
    """
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


def test_adversarial_hash_colliding_keys_never_reach_the_duplicate_table() -> None:
    """The structural property, asserted directly rather than by timing.

    Integer keys chosen as multiples of `sys.hash_info.modulus` all hash to
    0, so inserting them degrades set membership to a linear scan and
    restores the quadratic behaviour the set was introduced to remove.

    The defence is not a size ceiling and not a faster container: such a
    key is refused as contract-invalid on sight, so it never enters the
    table at all. The invariant is
    NON_STRING_CONSTRUCTED_KEY_NEVER_REACHES_HASH_TABLE, and it is checked
    here by asserting the refusal and its disposition -- wall-clock timing
    is kept only as a separate regression discriminator, never as proof of
    complexity.
    """
    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    modulus = sys.hash_info.modulus
    assert hash(modulus) == hash(2 * modulus) == 0, "precondition: these keys collide"

    document = "identity:\n" + "".join(f"  {modulus * (i + 1)}: v\n" for i in range(64))
    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        _parse_unambiguous_yaml_v2(document)
    assert excinfo.value.reason_code == TARGET_PROFILE_INVALID_REASON_V2


_SEMANTICALLY_INERT_CORPUS = {
    "plain": "a: 1\nb: {c: 2}\n",
    "anchor_alias": "a: &x {t: 1}\nb: *x\nc: *x\n",
    "merge": "a: &x {t: 1}\nb: {<<: *x}\n",
    "merge_override": "a: &x {t: 1}\nb: {<<: *x, t: 2}\n",
    "merge_sequence": "a: &x {p: 1}\nb: &y {q: 2}\nc: {<<: [*x, *y], r: 3}\n",
    "merge_chain": "a: &x {t: 1}\nb: &y {<<: *x, t: 2}\nc: {<<: *y, t: 3}\n",
    "alias_inside_sequence": "a: &x {t: 1}\nb: [*x, *x, {n: 1}]\n",
    "deep_nesting": "a: {b: {c: {d: {e: [1, 2, {f: 3}]}}}}\n",
    "shared_scalar_anchor": "a: &s hello\nb: *s\nc: [*s, *s]\n",
    "explicit_scalar_tags": "a: !!int 7\nb: !!float 1.5\nc: !!bool yes\nd: !!null ~\n",
    "sequence_of_mappings": "a:\n  - {x: 1}\n  - {y: 2}\n",
    "empty_containers": "a: {}\nb: []\n",
    "block_scalars": "a: |\n  line1\n  line2\nb: >\n  folded\n",
}


@pytest.mark.parametrize("label", sorted(_SEMANTICALLY_INERT_CORPUS))
def test_the_key_pre_pass_is_semantically_inert(label: str) -> None:
    """Constructing every key BEFORE `construct_document` must not change
    what the document becomes.

    The scan calls `construct_object(key_node, deep=True)` for every key
    shape, populating the loader's constructor cache before the document is
    built. That is the module's load-bearing assumption: if the pre-pass
    perturbed construction, the authority would validate one document and
    return another.

    Asserted against stock `yaml.safe_load` -- the reference semantics --
    rather than against a hand-written expectation.
    """
    text = _SEMANTICALLY_INERT_CORPUS[label]
    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    assert _parse_unambiguous_yaml_v2(text) == yaml.safe_load(text)


def test_the_pre_pass_preserves_alias_object_identity() -> None:
    """Two references to one anchor must remain the SAME object, as under
    stock `safe_load`. A pre-pass that rebuilt aliased nodes separately
    would silently turn shared structure into copies."""
    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    text = "a: &x {t: 1}\nb: *x\n"
    reference = yaml.safe_load(text)
    produced = _parse_unambiguous_yaml_v2(text)
    assert reference["a"] is reference["b"], "precondition: safe_load shares the object"
    assert produced["a"] is produced["b"]


# A mapping node with a NON-DEFAULT tag is not consumed as a mapping: its
# constructor selects an entry by TAG and discards the rest. Scanning its
# pairs as ordinary authored entries was wrong in BOTH directions.
_TAGGED_NODE_CORPUS_REFUSED = [
    # Two entries the constructor cannot tell apart -- same tag, different
    # raw values -- so the first silently wins and another reader could
    # take the second. Comparing (tag, value) saw them as distinct.
    ("same_tag_distinct_values", "m:\n  ? !!str {!!value a: x, !!value b: y}\n  : v\n"),
    ("duplicate_value_key", "m:\n  ? !!str {=: repo, =: default_branch}\n  : v\n"),
]
_TAGGED_NODE_CORPUS_ACCEPTED = [
    # The integer sibling is NEVER constructed: the string constructor
    # consumes the `=` entry and stops. Refusing the profile for a key that
    # never reaches a consumed mapping is over-refusal.
    ("unconsumed_sibling", "m:\n  ? !!str {=: repo, 123: ignored}\n  : v\n"),
]


@pytest.mark.parametrize(
    "label,text", _TAGGED_NODE_CORPUS_REFUSED, ids=[c[0] for c in _TAGGED_NODE_CORPUS_REFUSED]
)
def test_family_ambiguity_in_a_tagged_node_is_refused(label: str, text: str) -> None:
    """Entries a tagged constructor cannot discriminate are ambiguous.

    `!!str` selects by key TAG alone, so two entries sharing a tag mean two
    readings of the same bytes regardless of their raw values."""
    from app.agent_review.profile_loader_v2 import load_target_profile_text_v2

    with pytest.raises(TargetProfileLoadErrorV2) as excinfo:
        load_target_profile_text_v2(text)
    assert excinfo.value.reason_code == TARGET_PROFILE_UNREADABLE_REASON_V2, label


@pytest.mark.parametrize(
    "label,text", _TAGGED_NODE_CORPUS_ACCEPTED, ids=[c[0] for c in _TAGGED_NODE_CORPUS_ACCEPTED]
)
def test_family_unconsumed_entries_do_not_trigger_the_domain_rule(label: str, text: str) -> None:
    """The string-key domain rule applies to CONSUMED mappings only.

    A key that never reaches a consumed mapping cannot violate a contract
    about consumed mappings. Asserted as acceptance parity with stock
    `safe_load`, so the rule cannot quietly become an over-refusal."""
    from app.agent_review.profile_loader_v2 import _parse_unambiguous_yaml_v2

    assert _parse_unambiguous_yaml_v2(text) == yaml.safe_load(text), label
