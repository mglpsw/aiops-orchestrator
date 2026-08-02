"""Target-owned, deterministic semantic-grouping policy for AgentReview v2
(issue #106).

``TargetPoliciesV2.allowed_semantic_groups`` (``contracts_v2.py:906``) is an
ALLOWLIST -- it declares which semantic groups a target accepts, never how
to classify a given path into one of them. Without a deterministic,
target-owned classification rule, issue #109's run assembly would have to
invent that classification inside the engine itself -- exactly the
branching-by-repository issue #86 forbids.

``SemanticGroupingPolicyV2`` is a standalone artifact (deliberately NOT a
field folded into ``TargetProfileV2``, per this issue's own instruction not
to silently modify that frozen contract): a list of priority-ordered rules,
each mapping path patterns (and optionally referenced contract/artifact
ids) to a ``SemanticGroupV2``, plus an explicit ``fallback_group`` (or
none, meaning "block, never guess" -- see ``classify_semantic_group_v2``).

Two grandezas this module keeps distinct:

    validade estrutural do policy   = internally self-consistent (this
                                        module's own model validators)
    validade contra um profile      = every group/contract/artifact id the
                                        policy references actually exists
                                        in a specific TargetProfileV2
                                        (``bind_semantic_grouping_policy_to_target_profile_v2``)

A bare ``SemanticGroupingPolicyV2`` cannot check the second on its own --
same reasoning as ``RunFragmentCoverageEntryV2`` vs.
``bind_coverage_report_to_manifest_v2`` elsewhere in this codebase: a
Pydantic model has no way to reach out to another object at construction
time.

This module also defines ``EffectivePolicyBundleV2`` and
``compute_effective_policy_hash_v2``, closing the second problem this issue
names: ``profile_loader_v2.compute_policy_hash_v2`` hashes only
``profile.policies`` and does NOT include this policy just because a plan
document says it participates. The effective policy hash is the value
meant for ``RunIdentityV2.policy_hash`` once issue #109 assembles a real
run; ``compute_policy_hash_v2`` itself is UNCHANGED -- still documented and
tested as profile-policy-only, never silently redefined.

Deliberately out of scope here, per the issue: applying this policy to a
real diff/manifest (issue #109), and any change to
``planner_v2.plan_lossless_chunks_v2``'s ``semantic_group`` parameter,
which remains a caller-supplied value until #109 wires a real caller.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    RelativePattern,
    SafeIdentifier,
    SemanticGroupV2,
    SemanticGroupValue,
    Sha256,
    TargetPoliciesV2,
    TargetProfileV2,
)

SEMANTIC_GROUPING_NO_MATCH_REASON_V2 = "semantic_grouping_no_match"
SEMANTIC_GROUPING_AMBIGUOUS_MATCH_REASON_V2 = "semantic_grouping_ambiguous_match"
SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2 = "semantic_grouping_unknown_group"
SEMANTIC_GROUPING_UNKNOWN_CONTRACT_REASON_V2 = "semantic_grouping_unknown_contract"
SEMANTIC_GROUPING_UNKNOWN_ARTIFACT_REASON_V2 = "semantic_grouping_unknown_artifact"

_POLICY_SCHEMA_ID_V2 = "agent-review.semantic-grouping-policy.v2"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, and run_fragment_coverage_v2.py -- not a new rule.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class SemanticGroupingError(ValueError):
    """Raised for a semantic-grouping policy or classification failure.
    Carries a stable ``reason_code`` only -- never rule content, path
    values, or profile data."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class SemanticGroupingRuleV2(ContractV2Model):
    rule_id: SafeIdentifier
    semantic_group: SemanticGroupValue
    path_patterns: list[RelativePattern]
    contract_ids: list[SafeIdentifier]
    artifact_ids: list[SafeIdentifier]
    priority: int

    @model_validator(mode="after")
    def validate_rule(self) -> SemanticGroupingRuleV2:
        if not self.path_patterns:
            raise ValueError("a rule must declare at least one path pattern")
        for values, name in (
            (self.path_patterns, "path_patterns"),
            (self.contract_ids, "contract_ids"),
            (self.artifact_ids, "artifact_ids"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class SemanticGroupingPolicyMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.semantic-grouping-policy.v2"]
    schema_version: Literal[2]
    source: Literal["repo-semantic-grouping-policy"]
    rules: list[SemanticGroupingRuleV2]
    fallback_group: SemanticGroupValue | None

    @model_validator(mode="after")
    def validate_material(self) -> SemanticGroupingPolicyMaterialV2:
        if not self.rules:
            raise ValueError("a semantic grouping policy must declare at least one rule")
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("rule_id must be unique across the policy")
        return self


def canonical_semantic_grouping_policy_bytes_v2(
    value: SemanticGroupingPolicyMaterialV2 | Mapping[str, object],
) -> bytes:
    """Hash preimage: every validated policy field except ``policy_sha256``
    itself -- the same ``*Material*``/self-hashing split already
    established by ``ManifestMaterialV2``/``ManifestV2`` and
    ``ChunkPayloadMaterialV2``/``ChunkPayloadV2``, reused here rather than
    reinvented.

    ``rules`` is re-sorted by ``rule_id`` here, independent of construction
    order: ``sort_keys=True`` on the JSON dump already makes DICT key order
    irrelevant, but says nothing about LIST element order, and this policy
    is a list of rules. Without this, two policies built from the exact
    same rule set in a different order would hash differently, violating
    "reordering rules does not change the bytes" -- the same
    canonical-ordering discipline issue #105's ``PayloadSetV2`` applies to
    its own list-of-payloads field.

    The identical reasoning applies ONE LEVEL DEEPER: each rule's own
    ``path_patterns``/``contract_ids``/``artifact_ids`` are also lists, and
    every consumer in this module (``classify_semantic_group_v2``'s
    ``any(...)`` match, ``bind_semantic_grouping_policy_to_target_profile_v2``'s
    set-membership checks) already treats each of them as an unordered set
    -- ``SemanticGroupingRuleV2.validate_rule`` forbids duplicates within
    each, so sorting cannot silently collapse two distinct entries. Found
    by independent review: the first revision of this function sorted only
    the top-level ``rules`` list, so two rules identical except for
    ``contract_ids=["c1","c2"]`` vs. ``["c2","c1"]`` (the same set) hashed
    differently -- confirmed directly before this fix.
    """

    if isinstance(value, SemanticGroupingPolicyMaterialV2):
        material = value.model_dump(mode="json", exclude={"policy_sha256"})
    else:
        if not isinstance(value, Mapping):
            raise TypeError("semantic grouping policy must be a model or mapping")
        raw = dict(value)
        raw.pop("policy_sha256", None)
        material = SemanticGroupingPolicyMaterialV2.model_validate(raw).model_dump(mode="json")

    material = {**material, "rules": _canonical_semantic_grouping_rules_v2(material["rules"])}
    return _canonical_json_bytes_v2(material)


def _canonical_semantic_grouping_rules_v2(rules: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Shared by ``canonical_semantic_grouping_policy_bytes_v2`` and
    ``canonical_effective_policy_bytes_v2`` -- both hash a dumped ``rules``
    list and both must be independent of its construction order, at both the
    top level (list of rules) and one level deeper (each rule's own
    ``path_patterns``/``contract_ids``/``artifact_ids``, already validated
    duplicate-free/unordered sets by ``SemanticGroupingRuleV2.validate_rule``)."""

    def _canonical_rule(rule: Mapping[str, object]) -> dict[str, object]:
        return {
            **rule,
            "path_patterns": sorted(rule["path_patterns"]),
            "contract_ids": sorted(rule["contract_ids"]),
            "artifact_ids": sorted(rule["artifact_ids"]),
        }

    return sorted((_canonical_rule(rule) for rule in rules), key=lambda rule: rule["rule_id"])


def compute_semantic_grouping_policy_sha256_v2(
    value: SemanticGroupingPolicyMaterialV2 | Mapping[str, object],
) -> str:
    return hashlib.sha256(canonical_semantic_grouping_policy_bytes_v2(value)).hexdigest()


class SemanticGroupingPolicyV2(SemanticGroupingPolicyMaterialV2):
    policy_sha256: Sha256

    @model_validator(mode="after")
    def validate_policy_hash(self) -> SemanticGroupingPolicyV2:
        expected = compute_semantic_grouping_policy_sha256_v2(self)
        if self.policy_sha256 != expected:
            raise ValueError("policy_sha256 does not match the canonical policy material")
        return self


def bind_semantic_grouping_policy_to_target_profile_v2(
    policy: SemanticGroupingPolicyV2, profile: TargetProfileV2
) -> None:
    """Cross-check a policy against the target profile it is meant to
    apply to. Raises ``SemanticGroupingError`` fail-closed on any
    divergence; returns ``None`` on success.

    This proves the policy is not just internally coherent (already
    guaranteed by ``SemanticGroupingPolicyV2``'s own validators) but
    actually usable against THIS profile: every ``semantic_group`` a rule
    or the fallback names must be in ``profile.policies.allowed_semantic_groups``
    (never silently accepted just because it is a valid ``SemanticGroupV2``
    member), and every ``contract_id``/``artifact_id`` a rule references
    must exist in ``profile.contracts``/``profile.artifacts``.
    """

    allowed_groups = set(profile.policies.allowed_semantic_groups)
    known_contract_ids = {contract.contract_id for contract in profile.contracts}
    known_artifact_ids = {artifact.artifact_id for artifact in profile.artifacts}

    if policy.fallback_group is not None and policy.fallback_group not in allowed_groups:
        raise SemanticGroupingError(SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2)

    for rule in policy.rules:
        if rule.semantic_group not in allowed_groups:
            raise SemanticGroupingError(SEMANTIC_GROUPING_UNKNOWN_GROUP_REASON_V2)
        if not set(rule.contract_ids) <= known_contract_ids:
            raise SemanticGroupingError(SEMANTIC_GROUPING_UNKNOWN_CONTRACT_REASON_V2)
        if not set(rule.artifact_ids) <= known_artifact_ids:
            raise SemanticGroupingError(SEMANTIC_GROUPING_UNKNOWN_ARTIFACT_REASON_V2)


def classify_semantic_group_v2(policy: SemanticGroupingPolicyV2, *, path: str) -> SemanticGroupV2:
    """Deterministically classify a single path into a ``SemanticGroupV2``,
    using ``policy.rules`` alone -- never a branch on repository/target
    name.

    Matching: a rule matches ``path`` if ANY of its ``path_patterns``
    matches, using ``fnmatch.fnmatchcase`` (case-sensitive, deterministic,
    no locale dependency) against the full relative path.

    Precedence: among matching rules, the LOWEST ``priority`` value wins
    (declared once, deliberately, here -- not left to iteration order).
    Reordering ``policy.rules`` never changes the result, since the
    decision depends only on the set of matching rules and their
    ``priority`` values, not on list position.

    Fail-closed cases, never a guess:
      - zero rules match: use ``policy.fallback_group`` if set, else raise
        ``SemanticGroupingError(SEMANTIC_GROUPING_NO_MATCH_REASON_V2)``;
      - two or more matching rules share the WINNING (lowest) priority
        value: raise ``SemanticGroupingError(SEMANTIC_GROUPING_AMBIGUOUS_MATCH_REASON_V2)``
        -- this is the "priorities duplicadas E sobrepostas" case the issue
        names: same-priority rules that never match the same real path
        never reach this branch, so a shared priority alone is not an
        error; only a shared priority AMONG rules that both match the
        SAME given path is.
    """

    matching = [rule for rule in policy.rules if any(fnmatch.fnmatchcase(path, pattern) for pattern in rule.path_patterns)]
    if not matching:
        if policy.fallback_group is not None:
            return policy.fallback_group
        raise SemanticGroupingError(SEMANTIC_GROUPING_NO_MATCH_REASON_V2)

    best_priority = min(rule.priority for rule in matching)
    winners = [rule for rule in matching if rule.priority == best_priority]
    if len(winners) > 1:
        raise SemanticGroupingError(SEMANTIC_GROUPING_AMBIGUOUS_MATCH_REASON_V2)
    return winners[0].semantic_group


class EffectivePolicyBundleV2(ContractV2Model):
    """The full policy surface that actually governs a run: the target
    profile's frozen policies plus the target-owned semantic grouping
    policy. Neither field is optional -- a run without a
    ``SemanticGroupingPolicyV2`` has no deterministic classification and
    must not be assembled (issue #109)."""

    target_policies: TargetPoliciesV2
    semantic_grouping_policy: SemanticGroupingPolicyV2


def canonical_effective_policy_bytes_v2(bundle: EffectivePolicyBundleV2) -> bytes:
    """Hash preimage for the effective policy hash: the full bundle,
    INCLUDING the semantic grouping policy's own already-validated
    ``policy_sha256`` field -- any rule or priority change already changed
    that field, and this hash must change with it, so ``run_id`` changes
    too even when today's diff would classify identically under both
    policies.

    ``target_policies.required_checks`` and
    ``target_policies.allowed_semantic_groups`` are sorted here before
    hashing -- same reasoning as ``canonical_semantic_grouping_policy_bytes_v2``'s
    per-rule list sorting (found by the same round of independent review):
    both are already validated duplicate-free by
    ``TargetProfileV2.validate_profile_references``
    (``contracts_v2.py:946-947``), so they are semantically unordered sets,
    yet a naive JSON dump is order-sensitive. Without this, two profiles
    that are semantically identical but list these in a different textual
    order would produce a different effective policy hash. This does NOT
    touch ``profile_loader_v2.compute_policy_hash_v2`` itself -- that
    function's own order-sensitivity (a pre-existing characteristic of
    already-merged, frozen code) is explicitly out of scope for this issue,
    which forbids silently changing its semantics; this sort applies only
    to the bytes THIS function hashes.

    ``semantic_grouping_policy.rules`` (and each rule's own internal lists)
    are canonicalized the SAME way here as in
    ``canonical_semantic_grouping_policy_bytes_v2`` -- found by a follow-up
    independent review: ``bundle.model_dump()`` re-embeds the raw,
    unsorted ``rules`` list alongside the already order-independent
    ``policy_sha256``, so two policies with an IDENTICAL ``policy_sha256``
    but rules (or a rule's internal lists) constructed in a different order
    still produced a different effective hash before this fix -- confirmed
    directly. Reusing ``_canonical_semantic_grouping_rules_v2`` keeps both
    call sites' canonicalization from silently drifting apart.
    """

    dumped = bundle.model_dump(mode="json")
    dumped["target_policies"] = {
        **dumped["target_policies"],
        "required_checks": sorted(dumped["target_policies"]["required_checks"]),
        "allowed_semantic_groups": sorted(dumped["target_policies"]["allowed_semantic_groups"]),
    }
    dumped["semantic_grouping_policy"] = {
        **dumped["semantic_grouping_policy"],
        "rules": _canonical_semantic_grouping_rules_v2(dumped["semantic_grouping_policy"]["rules"]),
    }
    return _canonical_json_bytes_v2(dumped)


def compute_effective_policy_hash_v2(
    target_policies: TargetPoliciesV2, semantic_grouping_policy: SemanticGroupingPolicyV2
) -> str:
    """The value meant for ``RunIdentityV2.policy_hash`` once issue #109
    assembles a real run. Deliberately NOT a change to
    ``profile_loader_v2.compute_policy_hash_v2``, which remains
    profile-policy-only and unchanged -- callers that need the effective
    (profile + grouping) hash call this function explicitly instead."""

    bundle = EffectivePolicyBundleV2(
        target_policies=target_policies, semantic_grouping_policy=semantic_grouping_policy
    )
    return hashlib.sha256(canonical_effective_policy_bytes_v2(bundle)).hexdigest()
