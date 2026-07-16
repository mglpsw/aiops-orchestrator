# AgentEscala Target-Repository Contract

This document freezes the contract for the future AgentEscala workflow PR that
consumes the AIOps review quality gate. It does not change AgentEscala. The
canonical post-synthesis decision signal is
`$RUNNER_TEMP/agent/review-quality-gate.json`; `final-review.json` is an input
to the gate and is not a substitute for it.

## Ownership and boundary

`mglpsw/AgentEscala` is the target repository and product. It owns its domain
contracts, target-specific artifact generation, and the GitHub Actions
orchestration. Its AIOps integration must remain a thin wrapper: it consumes
sanitized artifacts, publishes the normal review or a conservative fallback
comment/summary, and does not reimplement parsing, synthesis, quality-gate,
telemetry, or false-positive logic.

`mglpsw/aiops-orchestrator` is the generic AgentReview engine and CT104
toolrepo. It produces `final-review.json`, `final-review.md`,
`review-quality-gate.json`, telemetry, and applicable false-positive artifacts.
The wrapper must not reinterpret findings or recalculate the gate.

`agent-router-api` is only an OpenAI-compatible gateway. The only permitted
endpoint is `/v1/chat/completions`; it does not execute workflows, shell,
deployments, or the review engine.

CT102 is production/runtime, never staging, and is never called by AgentReview.
This contract does not authorize deploys, restarts, SSH, Docker execution,
provider/Ollama calls, generated shell, or GitHub write APIs from AIOps.

## Checkout and output directories

The future AgentEscala workflow must check out `aiops-orchestrator` at
`$RUNNER_TEMP/aiops-orchestrator`, using an immutable full commit SHA or an
approved release tag. An operational checkout from a floating default branch
(including `main` or `master`) is not permitted. All generated inputs and outputs must be under
`$RUNNER_TEMP/agent`, never in the AgentEscala working tree.

The toolrepo sequence is:

```text
aiops-review-intake.py
  -> aiops-review-plan-chunks.py
  -> structured response JSON for each chunk
  -> aiops-review-parse-chunks.py
  -> aiops-review-synthesize.py
  -> aiops-review-quality-gate.py
  -> aiops-review-telemetry.py
  -> aiops-review-false-positives.py (when applicable)
```

The wrapper owns orchestration and publication only. It must not add a second
parser, synthesizer, gate, telemetry calculation, or false-positive decision.

## Gate input and validation

Before publication, the wrapper must read and schema-validate
`review-quality-gate.json` as `agent-review.quality-gate.v1`, with
`schema_version=1` and `source=aiops-review-quality-gate`. The decision artifact
must contain `status`, `normalized_verdict`, `manual_review_required`,
`blocked_reasons`, `warnings`, and `limitations`. It must also be checked for a
known version and known enum values before any PR comment or summary is written.

The three artifacts required for a final decision are:

```text
final-review.md
final-review.json
review-quality-gate.json
```

The wrapper must not treat `final-review.json` or any other artifact as the
decision authority when the gate is absent or invalid.

## Wrapper decision table

The conditions below are evaluated in order. No local finding promotion,
demotion, blocker confirmation, or verdict recalculation is permitted.

| Gate condition | Publication result |
| --- | --- |
| Valid gate; `status=passed` or `status=degraded`; `manual_review_required=false`; `normalized_verdict` is `approved`, `approve_with_minor_notes`, or `approve_with_required_followup` | Publish `final-review.md` and add a short `quality gate: passed` or `quality gate: degraded` banner matching `status`. If `status=degraded`, include the gate `limitations`. |
| Valid gate; `status=passed` or `status=degraded`; `manual_review_required=false`; `normalized_verdict=changes_requested` | Publish `final-review.md` as a blocking review. Preserve `blocked_reasons`; disclose `status=degraded` and its `limitations` when applicable; do not require the wrapper to reconfirm the blocker or promote/reduce findings locally. |
| `manual_review_required=true`, or `status=manual_review_required`, or `normalized_verdict=manual_review_required` | Publish a conservative `manual_review_required` comment/summary. Do not claim approval or a definitive `changes_requested`; state that human review of the available artifacts is required and link/reference those artifacts. |
| `status=failed` or `normalized_verdict=review_unavailable` | Publish a `review_unavailable` fallback. Do not publish `final-review.md` as a conclusive review; disclose `limitations` and reference available artifacts. |
| Gate missing, invalid JSON, incompatible schema, unknown version/status/verdict, or any impossible combination | Fail closed with `manual_review_required` or `review_unavailable`, using a specific reason code. Never use `final-review.json` as a replacement authority and never publish a conclusive review. |

`status=degraded` must never be hidden. It must be disclosed even when the
normalized verdict is otherwise publishable. A malformed or contradictory gate
is not a degraded success; it follows the fail-closed row.

## Artifact publication and sanitization

Upload is permitted only for artifacts that exist and pass sanitization:

```text
aiops-intake.json
redaction-report.json
semantic-chunk-plan.json
chunk-results.json
final-review.json
final-review.md
review-quality-gate.json
review-telemetry.json
false-positive-signatures.json
suggested-contract-updates.yaml
```

`suggested-contract-updates.yaml` is `manual_only`; `applied` must remain
`false`. AgentEscala never applies suggestions automatically. Raw model
responses, raw prompts, tokens, headers, cookies, and sensitive payloads must
never be published. Diagnostic artifacts should still be uploaded whenever
permitted, including for fallback decisions.

## Safe GitHub publication contract

The future wrapper PR must:

- use a stable HTML marker and update the prior idempotent comment instead of
  creating duplicates;
- separate read-only analysis from the job step that has write permission;
- grant only the minimum publication permissions;
- never use `pull_request_target` to execute untrusted PR code;
- never expose secrets to fork PRs; and
- validate the gate schema and version before publishing anything.

These are acceptance requirements for the future AgentEscala PR, not an
implementation in this repository.

## Future AgentEscala PR checklist

- [ ] Checkout AIOps by approved SHA/tag.
- [ ] Execute the toolrepo on CT104.
- [ ] Keep all outputs in `RUNNER_TEMP`.
- [ ] Use the quality gate as the authority.
- [ ] Smoke-test `passed`/approved.
- [ ] Smoke-test `changes_requested`.
- [ ] Smoke-test `manual_review_required`.
- [ ] Smoke-test `failed`/`review_unavailable`.
- [ ] Fail closed for a missing or invalid gate.
- [ ] Make the PR comment idempotent.
- [ ] Upload only sanitized artifacts.
- [ ] Make no call to CT102.
- [ ] Make no use of `/v1/chat/ingest`.
- [ ] Do not reimplement the gate.

## Suggested follow-up issue

**Title:** `feat(aiops): consume AIOps review quality gate in AgentReview thin wrapper`

**Suggested body:**

> Implement the AgentEscala-side thin wrapper for the frozen
> `AGENTESCALA_TARGET_REPO_CONTRACT.md` contract. Check out
> `aiops-orchestrator` by approved SHA/tag on CT104, consume the validated
> `review-quality-gate.json` artifact, publish an idempotent PR comment/summary,
> and fail closed for missing or invalid gates. Do not reimplement AgentReview,
> call CT102, use `/v1/chat/ingest`, expose fork secrets, or apply contract
> suggestions automatically.
