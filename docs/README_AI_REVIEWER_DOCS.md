# AgentReview documentation index

This directory contains the canonical documentation for the deterministic
AgentReview engine delivered in `v0.20.0`.

## Start here

- [`AGENT_REVIEW_ENGINE.md`](AGENT_REVIEW_ENGINE.md) — components, phases,
  CLIs and safety limits.
- [`AGENT_REVIEW_E2E_PIPELINE.md`](AGENT_REVIEW_E2E_PIPELINE.md) — complete
  CT104 pipeline and publication boundary.
- [`AGENT_REVIEW_QUALITY_GATE.md`](AGENT_REVIEW_QUALITY_GATE.md) — canonical
  gate schema, decision matrix and fail-closed behavior.
- [`AGENT_REVIEW_PR_BRIEF_AND_PAYLOADS.md`](AGENT_REVIEW_PR_BRIEF_AND_PAYLOADS.md)
  — sanitized PR brief, manifest and bounded per-chunk payloads.
- [`AGENT_REVIEW_TELEMETRY.md`](AGENT_REVIEW_TELEMETRY.md) — observational
  telemetry that never changes the verdict.
- [`AGENT_REVIEW_FALSE_POSITIVES.md`](AGENT_REVIEW_FALSE_POSITIVES.md) —
  deterministic signatures and manual-only contract suggestions.

## Target-repository integration

- [`AGENTESCALA_TARGET_REPO_CONTRACT.md`](AGENTESCALA_TARGET_REPO_CONTRACT.md)
  — immutable full-SHA checkout, gate validation and publication matrix.
- [`AGENTESCALA_TOOL_REPO_INTEGRATION.md`](AGENTESCALA_TOOL_REPO_INTEGRATION.md)
  — ownership and integration boundaries between AgentEscala and AIOps.
- [`AI_AGENTESCALA_REVIEW_CONTEXT.md`](AI_AGENTESCALA_REVIEW_CONTEXT.md) —
  target-domain review context.

AgentEscala owns orchestration, any approved optional Agent Router request and
GitHub publication. This toolrepo owns deterministic context selection,
parsing, synthesis, quality gate, telemetry and false-positive artifacts.

## Reviewer policy and historical context

- [`AI_REVIEWER_SEVERITY_AND_EVIDENCE_RULES.md`](AI_REVIEWER_SEVERITY_AND_EVIDENCE_RULES.md)
- [`AI_CONTEXT_AIOPS_ORCHESTRATOR_REVIEWER.md`](AI_CONTEXT_AIOPS_ORCHESTRATOR_REVIEWER.md)
- [`AI_GITHUB_AGENT_REVIEW_IMPROVEMENT_PROMPT.md`](AI_GITHUB_AGENT_REVIEW_IMPROVEMENT_PROMPT.md)
- [`GITHUB_AGENT.md`](GITHUB_AGENT.md)

These documents provide reviewer policy and evolution context. Where they
conflict with emitted schemas, tests or the quality-gate contract, the merged
code and tests are authoritative.

## Non-negotiable boundaries

- AgentReview runs only in CT104 dev/toolrepo mode.
- It never runs on CT102.
- AIOps AgentReview CLIs do not call providers, Agent Router, GitHub write APIs,
  Docker, SSH, deploy or restart.
- `/v1/chat/ingest` is forbidden for AgentReview.
- `review-quality-gate.json` is the post-synthesis authority;
  `final-review.json` is not a fallback authority.
- `suggested-contract-updates.yaml` remains `manual_only` with
  `applied: false`.
- Runtime checkout by a target repo must use a canonical lowercase full
  40-character commit SHA.

## Release reference

- [`RELEASE_V0_20_0.md`](RELEASE_V0_20_0.md)
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md)
- [`TESTING.md`](TESTING.md)
