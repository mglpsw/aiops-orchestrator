# AgentReview E2E Pipeline

Phase 05 connects the offline AgentReview Engine to AgentEscala's PR workflow.
The end-to-end flow is intentionally split across deterministic AIOps CLIs and
AgentEscala-owned orchestration.

## Sequence

```text
AgentEscala PR workflow on CT104
-> checkout AgentEscala
-> generate local artifacts under $RUNNER_TEMP/agent
-> checkout aiops-orchestrator as a pinned tool repo
-> validate dev/toolrepo environment
-> run aiops-review-intake.py
-> run aiops-review-plan-chunks.py
-> create one structured response JSON per chunk
-> optionally call Agent Router per chunk via /v1/chat/completions
-> run aiops-review-parse-chunks.py
-> run aiops-review-synthesize.py
-> optionally run aiops-review-quality-gate.py (planned #60 wiring)
-> upload sanitized artifacts
-> comment final-review.md on the PR
```

The AIOps CLIs do not call Agent Router, providers, GitHub APIs, CT102, Docker,
SSH, deploy, restart, or operational command execution.

## CLI Contract

Use the CT104 toolrepo environment:

```text
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

Then run:

```text
python scripts/aiops-review-intake.py \
  --target-repo mglpsw/AgentEscala \
  --repo-root "$GITHUB_WORKSPACE" \
  --agent-dir "$RUNNER_TEMP/agent" \
  --output "$RUNNER_TEMP/agent/aiops-intake.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json"

python scripts/aiops-review-plan-chunks.py \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --output "$RUNNER_TEMP/agent/semantic-chunk-plan.json"

python scripts/aiops-review-parse-chunks.py \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --responses-dir "$RUNNER_TEMP/agent/chunk-responses" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --output "$RUNNER_TEMP/agent/chunk-results.json"

python scripts/aiops-review-synthesize.py \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json" \
  --output-json "$RUNNER_TEMP/agent/final-review.json" \
  --output-md "$RUNNER_TEMP/agent/final-review.md"

# Optional/planned quality gate artifact. Full E2E wiring is tracked separately.
python scripts/aiops-review-quality-gate.py \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json" \
  --output "$RUNNER_TEMP/agent/review-quality-gate.json"
```

## Offline Contract Test

The AIOps repository validates this contract without network access:

```text
intake
-> plan-chunks
-> create fake chunk responses from semantic-chunk-plan.json
-> parse-chunks
-> synthesize
```

The test proves the six final artifacts exist and the markdown output is safe
for PR comments. It does not call Agent Router or any provider.

The quality-gate CLI is validated by focused unit/CLI tests in this PR. The
offline E2E contract remains unchanged here; adding
`review-quality-gate.json` to the full E2E flow is planned separately in #60.

## Upload Policy

Allowed workflow artifacts:

```text
aiops-intake.json
redaction-report.json
semantic-chunk-plan.json
chunk-results.json
final-review.json
final-review.md
review-quality-gate.json
sanitized diagnostics
```

Do not upload:

```text
full.diff raw
raw prompts
raw Router payloads
unvalidated raw Router responses
headers
real env dumps
secrets
tokens
cookies
```

## Release Criteria

`v0.19.0-rc.1` can be created manually after the AIOps contract PR merges and
the offline AgentReview tests pass.

`v0.19.0-rc.2` can be created manually after AgentEscala validates the thin
wrapper E2E on CT104 with the AIOps tool repo pinned by SHA.

CT102 runtime transition is not part of Phase 05.
