# AgentReview E2E Pipeline

Phase 05 connects the offline AgentReview Engine to AgentEscala's PR workflow.
The end-to-end flow is intentionally split across deterministic AIOps CLIs and
AgentEscala-owned orchestration. The target-repository consumption contract is
frozen in `docs/AGENTESCALA_TARGET_REPO_CONTRACT.md`.

## Sequence

```text
AgentEscala PR workflow on CT104
-> checkout AgentEscala
-> generate local artifacts under $RUNNER_TEMP/agent
-> checkout aiops-orchestrator at a full 40-character commit SHA
-> validate dev/toolrepo environment
-> run aiops-review-intake.py
-> run aiops-review-plan-chunks.py
-> create one structured response JSON per chunk
-> optionally call Agent Router per chunk via /v1/chat/completions
-> run aiops-review-parse-chunks.py
-> run aiops-review-synthesize.py
-> run aiops-review-quality-gate.py
-> run aiops-review-telemetry.py
-> run aiops-review-false-positives.py when applicable
-> upload sanitized artifacts
-> validate and consume review-quality-gate.json
-> publish a conclusive review or fail-closed fallback comment/summary
```

The AIOps CLIs do not call Agent Router, providers, GitHub APIs, CT102, Docker,
SSH, deploy, restart, or operational command execution. AgentEscala is a
thin-wrapper consumer and does not reimplement parsing, synthesis, gate,
telemetry, or false-positive logic.

## CLI Contract

Use the CT104 toolrepo environment and check out the tool repo at
`$RUNNER_TEMP/aiops-orchestrator` by full 40-character commit SHA only.
Operational refs must never be tag, branch, short SHA, or floating default
branch (`main` or `master`). A release tag can be used only in maintainer
selection flow (resolve tag -> verify commit -> commit the full SHA in PR).
Runtime tag resolution is prohibited.

Validate:

```text
AIOPS_ORCHESTRATOR_SHA matches ^[0-9a-fA-F]{40}$
git rev-parse HEAD == AIOPS_ORCHESTRATOR_SHA
```

If checkout cannot resolve the pinned SHA, stop the analysis job and do not
fallback to `master`.

Keep every output under
`$RUNNER_TEMP/agent`, never inside the AgentEscala working tree:

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

python scripts/aiops-review-quality-gate.py \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json" \
  --output "$RUNNER_TEMP/agent/review-quality-gate.json"

python scripts/aiops-review-telemetry.py \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --quality-gate "$RUNNER_TEMP/agent/review-quality-gate.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --chunk-plan "$RUNNER_TEMP/agent/semantic-chunk-plan.json" \
  --intake "$RUNNER_TEMP/agent/aiops-intake.json" \
  --redaction-report "$RUNNER_TEMP/agent/redaction-report.json" \
  --output "$RUNNER_TEMP/agent/review-telemetry.json"

python scripts/aiops-review-false-positives.py \
  --review-telemetry "$RUNNER_TEMP/agent/review-telemetry.json" \
  --quality-gate "$RUNNER_TEMP/agent/review-quality-gate.json" \
  --final-review "$RUNNER_TEMP/agent/final-review.json" \
  --chunk-results "$RUNNER_TEMP/agent/chunk-results.json" \
  --markers "$RUNNER_TEMP/agent/false-positive-markers.json" \
  --output "$RUNNER_TEMP/agent/false-positive-signatures.json" \
  --suggestions-output "$RUNNER_TEMP/agent/suggested-contract-updates.yaml"
```

## Gate consumption

`review-quality-gate.json` is the canonical post-synthesis signal. The wrapper
must validate it before publishing any conclusive or gate-derived review, and
apply the decision table in
`AGENTESCALA_TARGET_REPO_CONTRACT.md`. It must disclose `status=degraded`,
preserve gate limitations and blocked reasons, and fail closed for a missing,
invalid, unknown-version, or contradictory gate. `final-review.json` is not a
substitute authority.

When validation fails, publication must still run in fail-closed mode so the PR
gets a conservative status/comment (`manual_review_required` or
`review_unavailable`) derived from local validation results. It must never
publish a conclusive review from an invalid gate.

## Offline Contract Test

The AIOps repository validates this contract without network access:

```text
intake
-> plan-chunks
-> create fake chunk responses from semantic-chunk-plan.json
-> parse-chunks
-> synthesize
-> quality-gate
-> telemetry
```

The test proves the eight final artifacts exist and are written outside the
target repository:

```text
aiops-intake.json
redaction-report.json
semantic-chunk-plan.json
chunk-results.json
final-review.json
final-review.md
review-quality-gate.json
review-telemetry.json
false-positive-signatures.json (when applicable)
suggested-contract-updates.yaml (when applicable)
```

The E2E contract validates `review-quality-gate.json` against schema
`agent-review.quality-gate.v1`, keeps
`second_opinion_requested=false` and `second_opinion_status=not_required`, and
checks deterministic gate output for equivalent inputs. It also snapshots the
target fixture before and after the run to prove the target repository is not
modified, verifies the source fixture remains unchanged, and includes a
fail-closed assertion for production/runtime (`AIOPS_ENVIRONMENT=prod`) so the
pipeline never runs outside CT104 dev/toolrepo mode. The test does not call
Agent Router, any provider, CT102, Docker, SSH, deploy, restart, or GitHub
write APIs.

The telemetry artifact observes the already-produced final review and quality
gate outputs. It does not change verdicts, apply contracts, comment on PRs, or
persist historical data.

## Upload Policy

Allowed workflow artifacts, when present and sanitized:

```text
aiops-intake.json
redaction-report.json
semantic-chunk-plan.json
chunk-results.json
final-review.json
final-review.md
review-quality-gate.json
review-telemetry.json
sanitized diagnostics
```

`suggested-contract-updates.yaml` is manual-only and must retain
`applied: false`; AgentEscala never applies it automatically.

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
