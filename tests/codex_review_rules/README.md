# Codex review rule fixtures (issue #87, Escopo 4)

Deliberately minimal. Per this slice's own scoping decision (registered in
`docs/CODEX_REVIEW_WORKFLOW.md`), Escopo 4 is **not** a gate for #88's
calibration — #88 asks for the rules and subagents from #87, not this
meta-evaluation of them. This directory holds one worked example per
subagent (four total) to establish the format; growing it into full
coverage of every critical rule is explicit follow-up work, not part of
this slice's acceptance criteria.

## Format

Each rule file records, per the issue's own request:

- `rule_id`;
- `target_path` — the file(s)/pattern this rule concerns;
- `trigger` — a minimal fixture/patch that SHOULD cause the rule's
  subagent to raise a finding;
- `safe_counterexample` — a minimal, superficially similar change that
  should NOT trigger a finding;
- `unrelated_change` — a change to the same file that should generate no
  noise at all (the rule must not fire on things it has no opinion about);
- `codex_result` / `human_result` — left as `pending`. Populating these
  requires actually running Codex against each fixture and recording a real
  human/AIOps disposition, which is genuine calibration work for a future
  slice, not something this offline documentation slice fabricates.

## Rules recorded so far

| Rule ID | Subagent | File |
|---|---|---|
| RULE-001 | contract-reviewer | `RULE-001-hash-list-order.md` |
| RULE-002 | trust-boundary-reviewer | `RULE-002-network-access-widening.md` |
| RULE-003 | test-reviewer | `RULE-003-vacuous-exception-assertion.md` |
| RULE-004 | target-integration-reviewer | `RULE-004-target-name-branch.md` |
