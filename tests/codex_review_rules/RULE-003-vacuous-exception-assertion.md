# RULE-003 — a `pytest.raises` regression must assert the specific reason, not just the exception type

- `rule_id`: RULE-003
- `subagent`: test-reviewer
- `target_path`: `tests/agent_review/*.py`

## Trigger (should raise a finding)

```python
def test_cross_target_binding_is_rejected():
    with pytest.raises(SomeBindingError):
        bind_thing_to_other_thing(a, b)
```

Broad enough to pass even if the guard fails for a completely unrelated
reason — this test does not prove the SPECIFIC cross-check it claims to.

## Safe counterexample (should NOT raise a finding)

```python
def test_cross_target_binding_is_rejected():
    with pytest.raises(SomeBindingError) as excinfo:
        bind_thing_to_other_thing(a, b)
    assert excinfo.value.reason_code == EXPECTED_REASON_CODE
```

## Unrelated change (should generate no noise)

```python
def test_cross_target_binding_is_rejected():
    # Clarify docstring; no behavior change.
    """Proves binding a against a mismatched b fails closed."""
    with pytest.raises(SomeBindingError) as excinfo:
        bind_thing_to_other_thing(a, b)
    assert excinfo.value.reason_code == EXPECTED_REASON_CODE
```

## Results

- `codex_result`: pending — requires an actual Codex run against this
  fixture, not fabricated here.
- `human_result`: pending. (This exact pattern was found and fixed for
  real in PR #147's own two cross-target rejection tests, per an
  independent adversarial review — see that PR's history for the concrete
  before/after.)
