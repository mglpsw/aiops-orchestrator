# RULE-001 — hash preimage must sort semantically-unordered lists

- `rule_id`: RULE-001
- `subagent`: contract-reviewer
- `target_path`: `app/agent_review/*.py` (any `canonical_*_bytes_v2`/`compute_*_hash_v2` function)

## Trigger (should raise a finding)

```python
def canonical_widget_bytes_v2(material: dict) -> bytes:
    # BUG: sort_keys=True normalizes dict key order only. `rules` is a
    # list of independent items with no semantic order -- reordering the
    # input list must not change the hash, but this implementation lets it.
    return json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
```

## Safe counterexample (should NOT raise a finding)

```python
def canonical_widget_bytes_v2(material: dict) -> bytes:
    sortable = {**material, "rules": sorted(material["rules"], key=lambda r: r["rule_id"])}
    return json.dumps(
        sortable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
```

## Unrelated change (should generate no noise)

```python
def canonical_widget_bytes_v2(material: dict) -> bytes:
    sortable = {**material, "rules": sorted(material["rules"], key=lambda r: r["rule_id"])}
    # Renamed a local variable for clarity; no behavior change.
    payload = sortable
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
```

## Results

- `codex_result`: pending — requires an actual Codex run against this
  fixture, not fabricated here.
- `human_result`: pending.
