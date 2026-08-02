# RULE-004 — the engine must never branch on a target repository's name

- `rule_id`: RULE-004
- `subagent`: target-integration-reviewer
- `target_path`: `app/agent_review/*.py`

## Trigger (should raise a finding)

```python
def classify_something(repo: str, path: str) -> str:
    if repo == "mglpsw/AgentEscala":
        return "primary_backend_logic"
    if repo == "mglpsw/interleitos":
        return "api_schema_contract"
    return "unknown"
```

A real string-literal branch on a target's repository name, embedding
target-specific behavior directly in the engine.

## Safe counterexample (should NOT raise a finding)

```python
def classify_something(policy: SemanticGroupingPolicyV2, path: str) -> str:
    return classify_semantic_group_v2(policy, path=path).value
```

Behavior is entirely driven by the caller-supplied policy object, never by
a target name.

## Unrelated change (should generate no noise)

```python
# Comment mentioning a real consuming issue tracker for context, e.g.
# "closing AgentEscala#675's acceptance criterion" -- prose, not a branch.
def classify_something(policy: SemanticGroupingPolicyV2, path: str) -> str:
    return classify_semantic_group_v2(policy, path=path).value
```

## Results

- `codex_result`: pending — requires an actual Codex run against this
  fixture, not fabricated here.
- `human_result`: pending. (The automated version of this exact rule
  already runs as a real regression test:
  `tests/agent_review/test_v2_dual_target_e2e.py
  ::test_engine_never_branches_on_target_name`, confirmed non-vacuous by
  mutation testing in PR #147.)
