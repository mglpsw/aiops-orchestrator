from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import SemanticGroupV2, TargetProfileV2
from app.agent_review.semantic_grouping_policy_v2 import (
    SEMANTIC_GROUPING_AMBIGUOUS_MATCH_REASON_V2,
    SEMANTIC_GROUPING_NO_MATCH_REASON_V2,
    SEMANTIC_GROUPING_UNKNOWN_ARTIFACT_REASON_V2,
    SEMANTIC_GROUPING_UNKNOWN_CONTRACT_REASON_V2,
    SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2,
    EffectivePolicyBundleV2,
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    SemanticGroupingRuleV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
    classify_semantic_group_v2,
    compute_effective_policy_hash_v2,
    compute_semantic_grouping_policy_sha256_v2,
)


# -- fixture helpers ----------------------------------------------------------


def _rule(
    *,
    rule_id: str,
    semantic_group: SemanticGroupV2 = SemanticGroupV2.API_SCHEMA_CONTRACT,
    path_patterns: list[str] = ("app/*.py",),
    contract_ids: list[str] = (),
    artifact_ids: list[str] = (),
    priority: int = 0,
) -> SemanticGroupingRuleV2:
    return SemanticGroupingRuleV2(
        rule_id=rule_id,
        semantic_group=semantic_group,
        path_patterns=list(path_patterns),
        contract_ids=list(contract_ids),
        artifact_ids=list(artifact_ids),
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


def _target_profile(
    *,
    allowed_semantic_groups: list[str] = ("api_schema_contract", "tests"),
    required_checks: list[str] = ("pytest",),
) -> TargetProfileV2:
    return TargetProfileV2.model_validate(
        {
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
                "required_checks": list(required_checks),
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


# -- SemanticGroupingRuleV2 internal validity ----------------------------------


def test_rule_requires_at_least_one_path_pattern() -> None:
    with pytest.raises(ValidationError):
        SemanticGroupingRuleV2(
            rule_id="r1", semantic_group=SemanticGroupV2.TESTS, path_patterns=[], contract_ids=[], artifact_ids=[], priority=0
        )


def test_rule_rejects_duplicate_path_patterns() -> None:
    with pytest.raises(ValidationError):
        SemanticGroupingRuleV2(
            rule_id="r1", semantic_group=SemanticGroupV2.TESTS,
            path_patterns=["a/*.py", "a/*.py"], contract_ids=[], artifact_ids=[], priority=0,
        )


def test_rule_rejects_duplicate_contract_ids() -> None:
    with pytest.raises(ValidationError):
        SemanticGroupingRuleV2(
            rule_id="r1", semantic_group=SemanticGroupV2.TESTS,
            path_patterns=["a/*.py"], contract_ids=["c1", "c1"], artifact_ids=[], priority=0,
        )


def test_rule_rejects_duplicate_artifact_ids() -> None:
    with pytest.raises(ValidationError):
        SemanticGroupingRuleV2(
            rule_id="r1", semantic_group=SemanticGroupV2.TESTS,
            path_patterns=["a/*.py"], contract_ids=[], artifact_ids=["a1", "a1"], priority=0,
        )


# -- SemanticGroupingPolicyV2 internal validity --------------------------------


def test_policy_requires_at_least_one_rule() -> None:
    with pytest.raises(ValidationError):
        _policy(rules=[])


def test_policy_rejects_duplicate_rule_ids() -> None:
    rule_a = _rule(rule_id="dup", path_patterns=["a/*.py"])
    rule_b = _rule(rule_id="dup", path_patterns=["b/*.py"])
    with pytest.raises(ValidationError):
        _policy(rules=[rule_a, rule_b])


def test_policy_rejects_a_wrong_policy_sha256() -> None:
    rule = _rule(rule_id="r1")
    with pytest.raises(ValidationError):
        SemanticGroupingPolicyV2(
            schema_id="agent-review.semantic-grouping-policy.v2",
            schema_version=2,
            source="repo-semantic-grouping-policy",
            rules=[rule],
            fallback_group=None,
            policy_sha256="0" * 64,
        )


def test_policy_sha256_is_independent_of_rule_list_order() -> None:
    rule_a = _rule(rule_id="rule-a", path_patterns=["a/*.py"])
    rule_b = _rule(rule_id="rule-b", path_patterns=["b/*.py"])
    forward = _policy(rules=[rule_a, rule_b])
    backward = _policy(rules=[rule_b, rule_a])
    assert forward.policy_sha256 == backward.policy_sha256


def test_policy_sha256_is_independent_of_a_rules_own_internal_list_order() -> None:
    """Found by independent review of PR #137: the first revision sorted
    only the top-level rules list, so two rules identical except for
    contract_ids=["c1","c2"] vs. ["c2","c1"] (the same set -- duplicates
    are forbidden, and every consumer treats these as sets) hashed
    differently. path_patterns and artifact_ids are the same class of
    list and must be equally order-independent."""

    rule_forward = _rule(
        rule_id="r1", path_patterns=["a/*.py", "b/*.py"],
        contract_ids=["c1", "c2"], artifact_ids=["art-1", "art-2"],
    )
    rule_backward = _rule(
        rule_id="r1", path_patterns=["b/*.py", "a/*.py"],
        contract_ids=["c2", "c1"], artifact_ids=["art-2", "art-1"],
    )
    forward = _policy(rules=[rule_forward])
    backward = _policy(rules=[rule_backward])
    assert forward.policy_sha256 == backward.policy_sha256


# -- classify_semantic_group_v2 -----------------------------------------------


def test_classify_returns_the_matching_rules_group() -> None:
    policy = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.TESTS, path_patterns=["tests/*.py"])])
    assert classify_semantic_group_v2(policy, path="tests/test_a.py") is SemanticGroupV2.TESTS


def test_classify_returns_fallback_group_when_no_rule_matches() -> None:
    policy = _policy(
        rules=[_rule(rule_id="r1", path_patterns=["tests/*.py"])],
        fallback_group=SemanticGroupV2.UNKNOWN,
    )
    assert classify_semantic_group_v2(policy, path="docs/readme.md") is SemanticGroupV2.UNKNOWN


def test_classify_raises_no_match_when_no_rule_matches_and_no_fallback() -> None:
    policy = _policy(rules=[_rule(rule_id="r1", path_patterns=["tests/*.py"])], fallback_group=None)
    with pytest.raises(SemanticGroupingError) as excinfo:
        classify_semantic_group_v2(policy, path="docs/readme.md")
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_NO_MATCH_REASON_V2


def test_classify_picks_the_lowest_priority_among_matching_rules() -> None:
    rule_low = _rule(rule_id="low", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, path_patterns=["app/*.py"], priority=1)
    rule_high = _rule(rule_id="high", semantic_group=SemanticGroupV2.TESTS, path_patterns=["app/*.py"], priority=5)
    policy = _policy(rules=[rule_low, rule_high])
    assert classify_semantic_group_v2(policy, path="app/service.py") is SemanticGroupV2.API_SCHEMA_CONTRACT


def test_classify_raises_ambiguous_when_matching_rules_share_the_winning_priority() -> None:
    rule_a = _rule(rule_id="a", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, path_patterns=["app/*.py"], priority=1)
    rule_b = _rule(rule_id="b", semantic_group=SemanticGroupV2.TESTS, path_patterns=["app/*.py"], priority=1)
    policy = _policy(rules=[rule_a, rule_b])
    with pytest.raises(SemanticGroupingError) as excinfo:
        classify_semantic_group_v2(policy, path="app/service.py")
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_AMBIGUOUS_MATCH_REASON_V2


def test_classify_allows_shared_priority_among_rules_that_never_match_the_same_path() -> None:
    """'priorities duplicadas E sobrepostas falham fechado' -- duplicated
    WITHOUT overlap is fine: two rules may share a priority tier as long as
    they never both match the same real path."""

    rule_backend = _rule(rule_id="backend", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, path_patterns=["app/*.py"], priority=1)
    rule_tests = _rule(rule_id="tests", semantic_group=SemanticGroupV2.TESTS, path_patterns=["tests/*.py"], priority=1)
    policy = _policy(rules=[rule_backend, rule_tests])
    assert classify_semantic_group_v2(policy, path="app/service.py") is SemanticGroupV2.API_SCHEMA_CONTRACT
    assert classify_semantic_group_v2(policy, path="tests/test_service.py") is SemanticGroupV2.TESTS


def test_classify_result_is_independent_of_rule_list_order() -> None:
    rule_low = _rule(rule_id="low", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, path_patterns=["app/*.py"], priority=1)
    rule_high = _rule(rule_id="high", semantic_group=SemanticGroupV2.TESTS, path_patterns=["app/*.py"], priority=5)
    forward = _policy(rules=[rule_low, rule_high])
    backward = _policy(rules=[rule_high, rule_low])
    assert classify_semantic_group_v2(forward, path="app/service.py") == classify_semantic_group_v2(
        backward, path="app/service.py"
    )


# -- bind_semantic_grouping_policy_to_target_profile_v2 ------------------------


def test_bind_accepts_a_policy_whose_groups_and_references_are_all_known() -> None:
    profile = _target_profile(allowed_semantic_groups=["api_schema_contract", "tests"])
    rule = _rule(
        rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT,
        path_patterns=["app/*.py"], contract_ids=["contract.api"], artifact_ids=["full-diff"],
    )
    policy = _policy(rules=[rule], fallback_group=SemanticGroupV2.TESTS)
    bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)  # must not raise


def test_bind_rejects_a_rule_semantic_group_outside_allowed_semantic_groups() -> None:
    profile = _target_profile(allowed_semantic_groups=["tests"])
    policy = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT)])
    with pytest.raises(SemanticGroupingError) as excinfo:
        bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2


def test_bind_rejects_a_fallback_group_outside_allowed_semantic_groups() -> None:
    profile = _target_profile(allowed_semantic_groups=["tests"])
    policy = _policy(
        rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.TESTS)],
        fallback_group=SemanticGroupV2.API_SCHEMA_CONTRACT,
    )
    with pytest.raises(SemanticGroupingError) as excinfo:
        bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2


def test_bind_rejects_an_unknown_contract_id() -> None:
    profile = _target_profile(allowed_semantic_groups=["api_schema_contract"])
    policy = _policy(
        rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, contract_ids=["contract.unknown"])]
    )
    with pytest.raises(SemanticGroupingError) as excinfo:
        bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_UNKNOWN_CONTRACT_REASON_V2


def test_bind_rejects_an_unknown_artifact_id() -> None:
    profile = _target_profile(allowed_semantic_groups=["api_schema_contract"])
    policy = _policy(
        rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, artifact_ids=["artifact.unknown"])]
    )
    with pytest.raises(SemanticGroupingError) as excinfo:
        bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)
    assert excinfo.value.reason_code == SEMANTIC_GROUPING_UNKNOWN_ARTIFACT_REASON_V2


# -- EffectivePolicyBundleV2 / compute_effective_policy_hash_v2 ----------------


def test_effective_policy_bundle_requires_a_semantic_grouping_policy() -> None:
    """'policy ausente -> fail-closed': the bundle has no optional field --
    omitting semantic_grouping_policy is rejected at construction, not
    silently defaulted."""

    profile = _target_profile()
    with pytest.raises(ValidationError):
        EffectivePolicyBundleV2.model_validate({"target_policies": profile.policies.model_dump(mode="json")})


def test_compute_effective_policy_hash_v2_is_deterministic() -> None:
    profile = _target_profile()
    policy = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT)])
    first = compute_effective_policy_hash_v2(profile.policies, policy)
    second = compute_effective_policy_hash_v2(profile.policies, policy)
    assert first == second


def test_compute_effective_policy_hash_v2_changes_when_a_rule_changes_even_if_classification_is_unchanged() -> None:
    """A rule/priority change must change the effective policy hash (and
    therefore run_id, once #109 wires it in) even when today's diff would
    still classify identically under both policies."""

    profile = _target_profile()
    policy_a = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, priority=1)])
    policy_b = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, priority=2)])
    # Both policies classify "app/service.py" identically (single matching rule, group unchanged) --
    # only priority differs, which has no effect on this single-rule classification outcome.
    assert classify_semantic_group_v2(policy_a, path="app/service.py") == classify_semantic_group_v2(
        policy_b, path="app/service.py"
    )
    hash_a = compute_effective_policy_hash_v2(profile.policies, policy_a)
    hash_b = compute_effective_policy_hash_v2(profile.policies, policy_b)
    assert hash_a != hash_b


def test_compute_effective_policy_hash_v2_is_independent_of_target_policies_list_order() -> None:
    """Found by independent review of PR #137, same class as the rule-level
    fix above: TargetPoliciesV2.required_checks and .allowed_semantic_groups
    (contracts_v2.py:905-906) are validated duplicate-free/unordered sets by
    TargetProfileV2's own validator, but canonical_effective_policy_bytes_v2
    dumped the bundle with no list-sorting, so reordering either list
    changed the effective hash despite no semantic change. This does NOT
    touch profile_loader_v2.compute_policy_hash_v2, which stays frozen."""

    forward = _target_profile(
        allowed_semantic_groups=["api_schema_contract", "tests"],
        required_checks=["pytest", "lint"],
    )
    backward = _target_profile(
        allowed_semantic_groups=["tests", "api_schema_contract"],
        required_checks=["lint", "pytest"],
    )
    policy = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT)])
    assert compute_effective_policy_hash_v2(forward.policies, policy) == compute_effective_policy_hash_v2(
        backward.policies, policy
    )


def test_compute_effective_policy_hash_v2_is_independent_of_the_grouping_policys_rule_order() -> None:
    """Found by a follow-up independent review: bundle.model_dump() re-embeds
    the raw, unsorted semantic_grouping_policy.rules list alongside the
    already order-independent policy_sha256, so two policies with an
    IDENTICAL policy_sha256 but rules constructed in a different order (or
    a rule's own internal lists in a different order) still produced a
    different effective hash. This must vary rule order/internal-list
    order while holding policy_sha256 equal -- the earlier
    ...target_policies_list_order test reused the same single-rule policy
    for both sides and could not have caught this."""

    rule_a = _rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, path_patterns=["app/*.py"], priority=1)
    rule_b = _rule(rule_id="r2", semantic_group=SemanticGroupV2.TESTS, path_patterns=["tests/*.py"], priority=2)
    forward = _policy(rules=[rule_a, rule_b])
    backward = _policy(rules=[rule_b, rule_a])
    assert forward.policy_sha256 == backward.policy_sha256

    profile = _target_profile()
    assert compute_effective_policy_hash_v2(profile.policies, forward) == compute_effective_policy_hash_v2(
        profile.policies, backward
    )


def test_compute_policy_hash_v2_semantics_are_unchanged_by_this_module() -> None:
    """Issue #106's own requirement: profile_loader_v2.compute_policy_hash_v2
    must not be silently redefined -- it keeps hashing profile.policies
    alone, indifferent to any SemanticGroupingPolicyV2."""

    from app.agent_review.profile_loader_v2 import compute_policy_hash_v2

    profile = _target_profile()
    policy_a = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.API_SCHEMA_CONTRACT, priority=1)])
    policy_b = _policy(rules=[_rule(rule_id="r1", semantic_group=SemanticGroupV2.TESTS, priority=99)], fallback_group=SemanticGroupV2.TESTS)

    # profile_hash/policy_hash from the profile loader are computed from
    # the profile alone -- constructing two very different grouping
    # policies must not perturb them, and the effective hashes built from
    # each must still differ from one another.
    assert compute_policy_hash_v2(profile) == compute_policy_hash_v2(profile)
    assert compute_effective_policy_hash_v2(profile.policies, policy_a) != compute_effective_policy_hash_v2(
        profile.policies, policy_b
    )
