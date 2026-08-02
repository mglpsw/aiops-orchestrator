# RULE-002 — a workflow job must not gain network/provider access it didn't have

- `rule_id`: RULE-002
- `subagent`: trust-boundary-reviewer
- `target_path`: `.github/workflows/*.yml`, `scripts/github_agent_review.py`

## Trigger (should raise a finding)

```diff
 - name: Run GitHub agent review
   env:
     GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
+    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
   run: python scripts/github_agent_review.py
```

A previously offline/advisory job silently gains a real provider credential
with no accompanying scoping/justification in the diff.

## Safe counterexample (should NOT raise a finding)

```diff
 - name: Run GitHub agent review
   env:
     GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
+    AGENT_REVIEW_LLM_ENABLED: "false"
   run: python scripts/github_agent_review.py
```

Adding a feature flag that keeps the job's existing offline/advisory
posture unchanged.

## Unrelated change (should generate no noise)

```diff
 - name: Run GitHub agent review
   env:
     GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
+    # Bumped timeout for slower runners.
   timeout-minutes: 20
   run: python scripts/github_agent_review.py
```

## Results

- `codex_result`: pending — requires an actual Codex run against this
  fixture, not fabricated here.
- `human_result`: pending.
