# AgentReview v2 — semantic grouping policy and effective policy hash (#106)

Refs #106, child of the contract-closure epic #102. Blocks #109 (run/manifest
assembly), which will use `classify_semantic_group_v2` to populate
`ManifestChunkV2.semantic_group` for a real diff instead of accepting it as a
caller-supplied kwarg.

Delivers exactly what the issue asks: a target-owned, deterministic
classification contract, and the effective-policy-hash function that folds it
into `RunIdentityV2.policy_hash` once #109 assembles a real run. No planner
wiring, no run assembly, no classification of a real diff — that is #109's
job, deliberately kept out of this issue.

## Problem this closes

`TargetPoliciesV2.allowed_semantic_groups` (`contracts_v2.py:906`) is an
**allowlist** — it declares which `SemanticGroupV2` values a target accepts,
never how to classify a path into one of them. `planner_v2.plan_lossless_chunks_v2`
takes `semantic_group` as a plain caller-supplied string (`planner_v2.py:363`),
and no production caller exists. Without a deterministic, target-owned rule,
#109 would have had to invent classification logic inside the engine — the
exact repo-name branching #86 forbids.

Separately, `profile_loader_v2.compute_policy_hash_v2(profile)` hashes only
`profile.policies.model_dump(mode="json")`. A `SemanticGroupingPolicyV2`
artifact does not participate in that hash just because a plan document says
it should; a real `compute_effective_policy_hash_v2` function is required to
make it actually true.

## What this delivery adds

### `app/agent_review/semantic_grouping_policy_v2.py`

**`SemanticGroupingRuleV2`** — one classification rule: `rule_id`,
`semantic_group`, `path_patterns` (non-empty, unique, `fnmatch`-style glob),
`contract_ids`/`artifact_ids` (unique, reference ids resolved against a real
`TargetProfileV2` only at binding time — see below), `priority` (a plain
`int`; lower wins at classification time).

**`SemanticGroupingPolicyMaterialV2`** / **`SemanticGroupingPolicyV2`** — the
same `*Material*`/self-hashing split already established by
`ManifestMaterialV2`/`ManifestV2` and `ChunkPayloadMaterialV2`/`ChunkPayloadV2`,
reused rather than reinvented: `SemanticGroupingPolicyV2` adds `policy_sha256`
over every other field, validated against
`compute_semantic_grouping_policy_sha256_v2`. The hash preimage re-sorts
`rules` by `rule_id` before hashing — `sort_keys=True` already makes JSON
*dict* key order irrelevant, but says nothing about *list* element order, so
without this, two policies built from the identical rule set in a different
construction order would hash differently.

**Two grandezas this module keeps distinct**, matching the rest of v2's
discipline of not conflating "internally coherent" with "matches this
specific external object":

```text
validade estrutural do policy   = self-consistent on its own (rule_id
                                    uniqueness, non-empty path_patterns,
                                    policy_sha256 correctness)
validade contra um profile      = every semantic_group/contract_id/
                                    artifact_id the policy references
                                    actually exists in a SPECIFIC
                                    TargetProfileV2
```

A bare `SemanticGroupingPolicyV2` cannot check the second on its own — same
reasoning as `RunFragmentCoverageEntryV2` vs.
`bind_coverage_report_to_manifest_v2` (#104/#115) elsewhere in this codebase:
a Pydantic model has no way to reach out to another object at construction
time.

**`bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)`** —
the binding function. Raises `SemanticGroupingError` fail-closed if:

- any rule's `semantic_group`, or the `fallback_group`, is not in
  `profile.policies.allowed_semantic_groups` (`semantic_grouping_unknown_group`);
- any rule's `contract_ids` is not a subset of `profile.contracts`'
  `contract_id`s (`semantic_grouping_unknown_contract`);
- any rule's `artifact_ids` is not a subset of `profile.artifacts`'
  `artifact_id`s (`semantic_grouping_unknown_artifact`).

**`classify_semantic_group_v2(policy, path=...)`** — deterministic
classification of a single path, using `policy.rules` alone, never a branch
on repository/target name:

- a rule matches when ANY of its `path_patterns` matches `path`, via
  `fnmatch.fnmatchcase` (case-sensitive, no locale dependency);
- among matching rules, the LOWEST `priority` value wins;
- **zero rules match**: return `policy.fallback_group` if set, else raise
  `semantic_grouping_no_match` — never an implicit default;
- **two or more matching rules share the winning priority**: raise
  `semantic_grouping_ambiguous_match`. This is exactly the issue's
  "priorities duplicadas E sobrepostas falham fechado" — two rules sharing a
  priority value is fine on its own; it only becomes an error when both
  rules ALSO match the same real path. Two rules that share a priority but
  whose `path_patterns` never overlap (e.g. one covers `app/*.py`, the other
  `tests/*.py`) never reach this branch;
- reordering `policy.rules` never changes the result: the decision depends
  only on the set of matching rules and their priorities, never list
  position.

### `EffectivePolicyBundleV2` / `compute_effective_policy_hash_v2`

```text
EffectivePolicyBundleV2
- target_policies: TargetPoliciesV2
- semantic_grouping_policy: SemanticGroupingPolicyV2
```

Both fields are required — a run without a `SemanticGroupingPolicyV2` has no
deterministic classification and must not be assembled (#109); omitting it is
rejected at construction, not silently defaulted.

`compute_effective_policy_hash_v2(target_policies, semantic_grouping_policy)`
hashes the full bundle, **including** the grouping policy's own already-
validated `policy_sha256` field — any rule or priority change already
changed that field, and the effective hash (and therefore `run_id`, once
#109 wires it in) changes with it, **even when today's diff would classify
identically under both policies**. This is the value meant for
`RunIdentityV2.policy_hash`.

`profile_loader_v2.compute_policy_hash_v2` is **unchanged** — it keeps
hashing `profile.policies` alone, documented and tested as profile-policy-only.
Callers that need the effective (profile + grouping) hash call
`compute_effective_policy_hash_v2` explicitly instead; the two hashes are
never conflated.

## Contract topology (for #102's shared surface)

```text
consumed by #109 (run/manifest assembly):
  SemanticGroupingPolicyV2       -- via classify_semantic_group_v2
  EffectivePolicyBundleV2
  compute_effective_policy_hash_v2

produced by a target's own profile repository (not this issue):
  the on-disk SemanticGroupingPolicyV2 document itself -- loading it from a
  target checkout is #109's concern, mirroring profile_loader_v2's own
  fail-closed loading discipline for TargetProfileV2

untouched, frozen:
  TargetProfileV2 (contracts_v2.py) -- not modified to accommodate grouping
  profile_loader_v2.compute_policy_hash_v2 -- semantics unchanged
```

## Deliberately out of scope

- classifying a real diff/manifest against a policy (#109);
- wiring `planner_v2.plan_lossless_chunks_v2`'s `semantic_group` parameter to
  any real caller (#109);
- loading a `SemanticGroupingPolicyV2` document from a target checkout (a
  loader analogous to `profile_loader_v2.load_target_profile_v2`, if needed,
  is #109's concern);
- any CLI.

## Tests

`tests/agent_review/test_semantic_grouping_policy_v2.py` — 24 tests covering
every invariant listed above: rule/policy internal validity, `policy_sha256`
correctness and order-independence, classification (fallback, no-match,
priority precedence, ambiguous-tie, non-overlapping shared priority,
order-independence), binding against a `TargetProfileV2` (unknown group,
unknown contract, unknown artifact, accepted case), and the effective-hash
tests proving `compute_policy_hash_v2`'s semantics are untouched while the
effective hash still changes on a rule/priority change.
