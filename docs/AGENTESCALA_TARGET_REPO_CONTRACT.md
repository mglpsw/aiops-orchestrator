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
`$RUNNER_TEMP/aiops-orchestrator` using only a full 40-character Git commit SHA.
Operational refs must never be a branch, tag, short SHA, or floating default
branch (`main`/`master`). All generated inputs and outputs must be under
`$RUNNER_TEMP/agent`, never in the AgentEscala working tree.
`AIOPS_ORCHESTRATOR_SHA` must be stored in canonical lowercase form.

Release tags are allowed only in the human version-selection process:

1. maintainer selects a release tag;
2. resolves that tag to a commit;
3. verifies origin/release/expected commit;
4. records the verified full SHA in AgentEscala configuration; and
5. submits that SHA change in its own reviewable PR.

The workflow must never resolve tags dynamically at runtime.

The workflow must validate the configured SHA format:

```text
^[0-9a-f]{40}$
```

After checkout, the workflow must prove the effective revision is pinned:

```text
[[ "$AIOPS_ORCHESTRATOR_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git -C "$RUNNER_TEMP/aiops-orchestrator" rev-parse HEAD)" \
  = "$AIOPS_ORCHESTRATOR_SHA"
```

If the configured SHA is invalid or cannot be fetched, analysis must stop. It
must never fall back to `master` or any other floating ref. A moved tag must
never change the executed code path; updating AIOps always requires a reviewed
SHA change. Uppercase SHA input is not accepted.

Contractual checkout example:

```yaml
env:
  AIOPS_ORCHESTRATOR_SHA: <lowercase-40-character-commit-sha>

jobs:
  aiops-analysis:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: [self-hosted, ct104]
    steps:
      - name: Validate toolrepo SHA format
        run: |
          [[ "$AIOPS_ORCHESTRATOR_SHA" =~ ^[0-9a-f]{40}$ ]]

      - name: Checkout AIOps toolrepo
        uses: actions/checkout@<verified-full-commit-sha> # actions/checkout v4.x
        with:
          repository: mglpsw/aiops-orchestrator
          ref: ${{ env.AIOPS_ORCHESTRATOR_SHA }}
          path: ${{ runner.temp }}/aiops-orchestrator
          persist-credentials: false

      - name: Verify toolrepo checkout pin
        run: |
          test "$(git -C "$RUNNER_TEMP/aiops-orchestrator" rev-parse HEAD)" \
            = "$AIOPS_ORCHESTRATOR_SHA"
```

The action pin SHA and the toolrepo checkout SHA are separate controls. Both
must be reviewable. The concrete action SHA value is selected by the future
AgentEscala implementation PR; this contract intentionally avoids floating
action tags in examples.

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
known version and known enum values.

The three artifacts required for a final decision are:

```text
final-review.md
final-review.json
review-quality-gate.json
```

The wrapper must not treat `final-review.json` or any other artifact as the
decision authority when the gate is absent or invalid.

The wrapper must validate the gate before publishing any conclusive or
gate-derived review. If validation fails, it must still publish a conservative
fail-closed fallback generated from the local validation result, without
trusting fields from the invalid gate.

## Wrapper decision table

The conditions below are evaluated in order. No local finding promotion,
demotion, blocker confirmation, or verdict recalculation is permitted.

Publication classes:

- **Conclusive publication**: `final-review.md` plus `approved`,
  `approve_with_minor_notes`, `approve_with_required_followup`, or
  `changes_requested`. This is allowed only after successful gate validation
  (JSON, schema ID/version, source, status, normalized verdict,
  `manual_review_required`, and allowed combinations).
- **Fail-closed publication**: mandatory fallback when gate validation fails.
  This path must be generated from local validation errors only; it must not
  trust invalid/missing gate fields as authority.

Fail-closed reason codes:

- `gate_missing`
- `gate_json_invalid`
- `gate_schema_invalid`
- `gate_version_unsupported`
- `gate_source_invalid`
- `gate_status_unknown`
- `gate_verdict_unknown`
- `gate_combination_invalid`
- `gate_validation_failed`
- `toolrepo_sha_invalid`
- `toolrepo_checkout_failed`
- `toolrepo_sha_mismatch`

Rows prefixed with **Valid gate** are evaluated only after schema/source/version
and enum validation, plus allowed-combination validation. An invalid gate must
never be treated as valid only because it contains `manual_review_required=true`.

| Gate condition | Publication result |
| --- | --- |
| Valid gate; `status=passed`; `manual_review_required=false`; `normalized_verdict` is `approved`, `approve_with_minor_notes`, or `approve_with_required_followup`; `blocked_reasons` empty | Publish `final-review.md` as conclusive non-blocking output. |
| Valid gate; `status=passed`; `manual_review_required=false`; `normalized_verdict=changes_requested`; `blocked_reasons` non-empty | Publish `final-review.md` as conclusive blocking output. |
| Valid gate; `status=degraded`; `manual_review_required=false`; `normalized_verdict=changes_requested`; `blocked_reasons` non-empty; `limitations` non-empty | Publish conclusive blocking output and disclose all limitations. The validated gate is authoritative; the wrapper must not reconfirm blocker evidence. |
| Valid gate; `status=manual_review_required`; `normalized_verdict=manual_review_required`; `manual_review_required=true` | Publish non-conclusive `manual_review_required` fallback with artifact references. |
| Valid gate; `status=failed`; `normalized_verdict=review_unavailable`; `manual_review_required=true` | Publish non-conclusive `review_unavailable` fallback with artifact references. |
| Any other combination, or gate missing/invalid/incompatible/unknown/contradictory | Use `gate_combination_invalid` (or specific local reason code) and publish deterministic fail-closed result only: `publication_result=review_unavailable`, `manual_review_required=true`, `publication_class=fail_closed`. Never use `final-review.json` as replacement authority and never copy raw invalid payload into publication. |

`status=degraded` must never be hidden and can never be used for conclusive
approval. `changes_requested` requires non-empty `blocked_reasons`. A malformed
or contradictory gate is not a degraded success; it follows the fail-closed row.
Any non-blocking verdict with non-empty `blocked_reasons` is an invalid
combination and must follow fail-closed publication.

For the validated `status=degraded` + `normalized_verdict=changes_requested`
combination, blocker reliability is determined internally by AIOps before the
gate is emitted. The wrapper only validates schema/source/version/enums and
allowed combinations, verifies non-empty `blocked_reasons`, and discloses
`limitations`. It must never inspect `final-review.json` to reconfirm blockers.

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
- block fork PRs at job level before allocating self-hosted CT104 runners:
  `if: github.event.pull_request.head.repo.full_name == github.repository`;
- separate read-only analysis from the job step that has write permission;
- grant only the minimum publication permissions;
- never use `pull_request_target` to execute untrusted PR code;
- never expose secrets to fork PRs;
- set `persist-credentials: false` on analysis checkouts; and
- validate the gate before publishing any conclusive or gate-derived review.
  When validation fails, still publish conservative fail-closed fallback from
  local validation output without trusting invalid gate fields.

These are acceptance requirements for the future AgentEscala PR, not an
implementation in this repository.

## Future AgentEscala PR checklist

- [ ] Checkout AIOps by full 40-character commit SHA only.
- [ ] Store `AIOPS_ORCHESTRATOR_SHA` in canonical lowercase form.
- [ ] Validate `AIOPS_ORCHESTRATOR_SHA` against `^[0-9a-f]{40}$`.
- [ ] Verify `[[ "$AIOPS_ORCHESTRATOR_SHA" =~ ^[0-9a-f]{40}$ ]]`.
- [ ] Verify `test "$(git -C "$RUNNER_TEMP/aiops-orchestrator" rev-parse HEAD)" = "$AIOPS_ORCHESTRATOR_SHA"` after checkout.
- [ ] Never resolve tags dynamically during workflow execution.
- [ ] Execute the toolrepo on CT104.
- [ ] Keep all outputs in `RUNNER_TEMP`.
- [ ] Use the quality gate as the authority.
- [ ] Smoke-test `passed`/approved.
- [ ] Smoke-test `changes_requested`.
- [ ] Smoke-test `manual_review_required`.
- [ ] Smoke-test `failed`/`review_unavailable`.
- [ ] Publish fail-closed fallback for gate-validation failures (missing,
  malformed, incompatible, unknown, contradictory).
- [ ] Make the PR comment idempotent.
- [ ] Upload only sanitized artifacts.
- [ ] Make no call to CT102.
- [ ] Make no use of `/v1/chat/ingest`.
- [ ] Do not reimplement the gate.
- [ ] Exclude fork PRs from CT104 jobs before checkout/execution/artifacts/router/secrets.

## Suggested follow-up issue

**Title:** `feat(aiops): consume AIOps review quality gate in AgentReview thin wrapper`

**Suggested body:**

> Implement the AgentEscala-side thin wrapper for the frozen
> `AGENTESCALA_TARGET_REPO_CONTRACT.md` contract. Check out
> `aiops-orchestrator` by a canonical lowercase full 40-character SHA on CT104
> (no branch/tag/short SHA operational refs), consume the validated
> `review-quality-gate.json` artifact, publish an idempotent PR comment/summary
> for conclusive outcomes, and publish conservative fail-closed fallback from
> local validation errors when the gate is missing or invalid. Do not
> reimplement AgentReview, call CT102, use `/v1/chat/ingest`, expose fork
> secrets, or apply contract
> suggestions automatically.
