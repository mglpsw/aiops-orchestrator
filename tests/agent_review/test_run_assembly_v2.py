from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import SemanticGroupV2, TargetProfileV2, compute_run_id
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2, ParsedHunkV2
from app.agent_review.run_assembly_v2 import (
    RUN_ASSEMBLY_GLOBAL_BUDGET_EXHAUSTED_REASON_V2,
    RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2,
    RUN_ASSEMBLY_REQUIRED_PATH_MISSING_REASON_V2,
    RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2,
    RUN_ASSEMBLY_UNKNOWN_MUST_REVIEW_ARTIFACT_REASON_V2,
    RunAssemblyError,
    assemble_manifest_from_diff_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    compute_semantic_grouping_policy_sha256_v2,
)


# -- fixture helpers ------------------------------------------------------------


def _hunk(*, old_start: int, old_lines: int, new_start: int, new_lines: int, seed: str) -> ParsedHunkV2:
    return ParsedHunkV2(
        old_start=old_start,
        old_lines=old_lines,
        new_start=new_start,
        new_lines=new_lines,
        diff_sha256=hashlib.sha256(seed.encode()).hexdigest(),
        diff_chars=40,
    )


def _file_diff(
    *,
    path: str,
    hunks: tuple[ParsedHunkV2, ...] = (),
    is_binary: bool = False,
    is_submodule: bool = False,
    truncated: bool = False,
) -> ParsedFileDiffV2:
    return ParsedFileDiffV2(
        old_path=path,
        new_path=path,
        change_type="modified",
        is_binary=is_binary,
        is_submodule=is_submodule,
        similarity_index=None,
        old_no_newline_at_eof=False,
        new_no_newline_at_eof=False,
        hunks=hunks,
        truncated=truncated,
    )


def _rule(
    *,
    rule_id: str,
    semantic_group: SemanticGroupV2,
    path_patterns: list[str],
    priority: int = 0,
) -> SemanticGroupingRuleV2:
    return SemanticGroupingRuleV2(
        rule_id=rule_id,
        semantic_group=semantic_group,
        path_patterns=path_patterns,
        contract_ids=[],
        artifact_ids=[],
        priority=priority,
    )


def _policy(*, rules: list[SemanticGroupingRuleV2], fallback_group: SemanticGroupV2 | None = None) -> SemanticGroupingPolicyV2:
    material = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": rules,
        "fallback_group": fallback_group,
    }
    policy_sha256 = compute_semantic_grouping_policy_sha256_v2(
        {**material, "rules": [rule.model_dump(mode="json") for rule in rules]}
    )
    return SemanticGroupingPolicyV2(**material, policy_sha256=policy_sha256)


def _single_group_policy() -> SemanticGroupingPolicyV2:
    return _policy(
        rules=[_rule(rule_id="all", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC, path_patterns=["*"])]
    )


def _two_group_policy() -> SemanticGroupingPolicyV2:
    return _policy(
        rules=[
            _rule(rule_id="app", semantic_group=SemanticGroupV2.PRIMARY_BACKEND_LOGIC, path_patterns=["app/*.py"], priority=1),
            _rule(rule_id="tests", semantic_group=SemanticGroupV2.TESTS, path_patterns=["tests/*.py"], priority=1),
        ]
    )


def _profile(
    *,
    max_chunks: int = 32,
    allowed_semantic_groups: list[str] = ("primary_backend_logic", "tests"),
    must_review_paths: list[str] = (),
    must_review_patterns: list[str] = (),
    must_review_artifact_ids: list[str] = (),
    artifacts: list[dict] = None,
) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
            "schema_id": "agent-review.target-profile.v2",
            "schema_version": 2,
            "source": "repo-profile",
            "identity": {"repo": "mglpsw/aiops-orchestrator", "default_branch": "master"},
            "artifacts": artifacts
            or [
                {
                    "artifact_id": "full-diff",
                    "path": "artifacts/full.diff",
                    "kind": "diff",
                    "required": True,
                    "max_bytes": 1000000,
                }
            ],
            "budgets": {
                "max_chunks": max_chunks,
                "total_prompt_chars": 250000,
                "max_chars_per_chunk": 24000,
                "max_files_per_chunk": 50,
                "max_contracts_per_chunk": 50,
            },
            "must_review": {
                "paths": list(must_review_paths),
                "patterns": list(must_review_patterns),
                "artifact_ids": list(must_review_artifact_ids),
                "minimum_coverage": "complete",
            },
            "policies": {
                "network_policy": "forbidden",
                "fail_closed": True,
                "redaction_required": True,
                "allow_partial_coverage": False,
                "required_checks": ["pytest"],
                "allowed_semantic_groups": list(allowed_semantic_groups),
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
    )


def _identity_kwargs(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 129,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "evidence_hash": "d" * 64,
        "max_lines_per_chunk": 100,
    }
    raw.update(overrides)
    return raw


def _assemble(*, file_diffs, profile, grouping_policy, **overrides):
    kwargs = _identity_kwargs(**overrides)
    return assemble_manifest_from_diff_v2(
        file_diffs,
        profile=profile,
        grouping_policy=grouping_policy,
        **kwargs,
    )


# -- basic assembly --------------------------------------------------------------


def test_assembles_a_real_manifest_and_identity_from_a_single_file_diff() -> None:
    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    outcome = _assemble(file_diffs=[file_diff], profile=_profile(), grouping_policy=_single_group_policy())
    assert outcome.state == "assembled"
    manifest = outcome.manifest
    assert manifest is not None
    assert manifest.expected_files == ["app/a.py"]
    assert manifest.run_id == compute_run_id(manifest.identity)
    assert outcome.excluded_paths == ()


def test_two_targets_with_different_policies_produce_different_groups_without_engine_changes() -> None:
    """Acceptance criterion, verbatim: 'DOIS TARGETS COM POLICIES
    DIFERENTES produzem grupos diferentes sem nenhuma alteração no
    engine.'"""

    file_diffs = [
        _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),)),
    ]
    profile = _profile()

    all_primary = _single_group_policy()
    outcome_a = _assemble(file_diffs=file_diffs, profile=profile, grouping_policy=all_primary)
    assert {chunk.semantic_group.value for chunk in outcome_a.manifest.chunks} == {"primary_backend_logic"}

    other_policy = _policy(
        rules=[_rule(rule_id="all", semantic_group=SemanticGroupV2.TESTS, path_patterns=["*"])]
    )
    other_profile = _profile(allowed_semantic_groups=["tests"])
    outcome_b = _assemble(file_diffs=file_diffs, profile=other_profile, grouping_policy=other_policy)
    assert {chunk.semantic_group.value for chunk in outcome_b.manifest.chunks} == {"tests"}


def test_multi_group_orchestration_merges_two_semantic_groups_into_one_manifest() -> None:
    file_diffs = [
        _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),)),
        _file_diff(path="tests/test_a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="b"),)),
    ]
    outcome = _assemble(file_diffs=file_diffs, profile=_profile(), grouping_policy=_two_group_policy())
    assert outcome.state == "assembled"
    groups = {chunk.semantic_group.value for chunk in outcome.manifest.chunks}
    assert groups == {"primary_backend_logic", "tests"}
    assert sorted(outcome.manifest.expected_files) == ["app/a.py", "tests/test_a.py"]


def test_material_fragment_change_changes_both_manifest_hash_and_run_id() -> None:
    """Acceptance criterion, verbatim: 'alteração material de um fragment
    muda manifest_hash E run_id.'"""

    file_diff_a = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    file_diff_b = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="different"),))

    outcome_a = _assemble(file_diffs=[file_diff_a], profile=_profile(), grouping_policy=_single_group_policy())
    outcome_b = _assemble(file_diffs=[file_diff_b], profile=_profile(), grouping_policy=_single_group_policy())

    assert outcome_a.manifest.identity.manifest_hash != outcome_b.manifest.identity.manifest_hash
    assert outcome_a.manifest.run_id != outcome_b.manifest.run_id


def test_same_inputs_produce_a_byte_identical_manifest() -> None:
    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    outcome_a = _assemble(file_diffs=[file_diff], profile=_profile(), grouping_policy=_single_group_policy())
    outcome_b = _assemble(file_diffs=[file_diff], profile=_profile(), grouping_policy=_single_group_policy())
    assert outcome_a.manifest.model_dump(mode="json") == outcome_b.manifest.model_dump(mode="json")


# -- must_review resolution -------------------------------------------------------


def test_must_review_from_explicit_paths_marks_fragments_coverage_required() -> None:
    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    outcome = _assemble(
        file_diffs=[file_diff], profile=_profile(must_review_paths=["app/a.py"]), grouping_policy=_single_group_policy()
    )
    assert outcome.manifest.must_review_files == ["app/a.py"]
    assert all(fragment.coverage_required for fragment in outcome.manifest.fragments)


def test_must_review_from_a_pattern_marks_a_matching_path() -> None:
    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    outcome = _assemble(
        file_diffs=[file_diff],
        profile=_profile(must_review_patterns=["app/*.py"]),
        grouping_policy=_single_group_policy(),
    )
    assert outcome.manifest.must_review_files == ["app/a.py"]


def test_must_review_resolves_an_artifact_id_to_its_configured_path() -> None:
    file_diff = _file_diff(path="config/settings.py", hunks=(_hunk(old_start=1, old_lines=3, new_start=1, new_lines=3, seed="a"),))
    profile = _profile(
        must_review_artifact_ids=["settings-artifact"],
        artifacts=[
            {
                "artifact_id": "settings-artifact",
                "path": "config/settings.py",
                "kind": "text",
                "required": True,
                "max_bytes": 1000,
            }
        ],
    )
    outcome = _assemble(file_diffs=[file_diff], profile=profile, grouping_policy=_single_group_policy())
    assert outcome.manifest.must_review_files == ["config/settings.py"]


def test_an_unknown_must_review_artifact_id_fails_closed() -> None:
    """TargetProfileV2.validate_profile_references already guarantees
    must_review.artifact_ids references only known artifacts, so this is
    unreachable through any normally-constructed profile. model_construct
    bypasses that validator to simulate the defense-in-depth guard firing
    without waiting for a real, otherwise-impossible defect."""

    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    profile = _profile()
    tampered_must_review = profile.must_review.model_copy(update={"artifact_ids": ["does-not-exist"]})
    profile = profile.model_copy(update={"must_review": tampered_must_review})
    with pytest.raises(RunAssemblyError) as excinfo:
        _assemble(file_diffs=[file_diff], profile=profile, grouping_policy=_single_group_policy())
    assert excinfo.value.reason_code == RUN_ASSEMBLY_UNKNOWN_MUST_REVIEW_ARTIFACT_REASON_V2


# -- paths that never produce a fragment -----------------------------------------


def test_a_non_required_binary_path_is_silently_excluded() -> None:
    text_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    binary_diff = _file_diff(path="assets/logo.png", is_binary=True)
    outcome = _assemble(file_diffs=[text_diff, binary_diff], profile=_profile(), grouping_policy=_single_group_policy())
    assert outcome.state == "assembled"
    assert "assets/logo.png" in outcome.excluded_paths
    assert "assets/logo.png" not in outcome.manifest.expected_files


def test_a_required_binary_path_blocks_the_whole_assembly() -> None:
    binary_diff = _file_diff(path="assets/logo.png", is_binary=True)
    profile = _profile(must_review_paths=["assets/logo.png"])
    outcome = _assemble(file_diffs=[binary_diff], profile=profile, grouping_policy=_single_group_policy())
    assert outcome.state == "blocked_pipeline"
    assert outcome.manifest is None
    assert outcome.blocked_reason.reason_code == RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2
    assert outcome.blocked_reason.affected_paths == ("assets/logo.png",)


def test_a_required_path_expected_but_missing_from_the_diff_blocks_the_whole_assembly() -> None:
    file_diff = _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),))
    profile = _profile(must_review_paths=["app/missing.py"])
    outcome = _assemble(
        file_diffs=[file_diff],
        profile=profile,
        grouping_policy=_single_group_policy(),
        expected_paths=frozenset({"app/a.py", "app/missing.py"}),
    )
    assert outcome.state == "blocked_pipeline"
    assert outcome.blocked_reason.reason_code == RUN_ASSEMBLY_REQUIRED_PATH_MISSING_REASON_V2
    assert outcome.blocked_reason.affected_paths == ("app/missing.py",)


# -- global budget across semantic groups ----------------------------------------


def test_global_budget_is_respected_across_semantic_groups() -> None:
    """Acceptance criterion, verbatim: 'budget global respeitado entre
    grupos.' Each group's hunk is small enough to fit alone, but the
    global max_chunks=1 cannot host both groups' chunks."""

    file_diffs = [
        _file_diff(path="app/a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="a"),)),
        _file_diff(path="tests/test_a.py", hunks=(_hunk(old_start=1, old_lines=5, new_start=1, new_lines=5, seed="b"),)),
    ]
    outcome = _assemble(
        file_diffs=file_diffs, profile=_profile(max_chunks=1), grouping_policy=_two_group_policy()
    )
    assert outcome.state == "blocked_pipeline"
    # Groups are processed in sorted semantic_group order ("primary_backend_logic"
    # < "tests"): the first group consumes the entire global budget (1 chunk),
    # leaving the second group with zero remaining -- deterministically the
    # GLOBAL_BUDGET_EXHAUSTED path, not a per-group packing failure.
    assert outcome.blocked_reason.reason_code == RUN_ASSEMBLY_GLOBAL_BUDGET_EXHAUSTED_REASON_V2
    assert outcome.blocked_reason.affected_paths == ("tests/test_a.py",)


def test_insufficient_max_chunks_blocks_the_pipeline() -> None:
    """Acceptance criterion, verbatim: 'max_chunks insuficiente ->
    blocked_pipeline.' A single hunk far larger than max_lines_per_chunk,
    with max_chunks=1, cannot be packed."""

    big_hunk = _hunk(old_start=1, old_lines=1000, new_start=1, new_lines=1000, seed="big")
    file_diff = _file_diff(path="app/a.py", hunks=(big_hunk,))
    outcome = _assemble(
        file_diffs=[file_diff],
        profile=_profile(must_review_paths=["app/a.py"], max_chunks=1),
        grouping_policy=_single_group_policy(),
        max_lines_per_chunk=10,
    )
    assert outcome.state == "blocked_pipeline"
    assert outcome.blocked_reason.reason_code == RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2


# -- no repository-name branching -------------------------------------------------


def test_module_never_references_agentescala_or_interleitos_by_name() -> None:
    """Acceptance criterion, verbatim: 'grep provando ausência de
    AgentEscala/interleitos no módulo.'"""

    import inspect

    from app.agent_review import run_assembly_v2

    source = inspect.getsource(run_assembly_v2).lower()
    assert "agentescala" not in source
    assert "interleitos" not in source
