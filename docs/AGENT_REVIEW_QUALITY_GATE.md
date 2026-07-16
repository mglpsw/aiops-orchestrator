# AgentReview Quality Gate

The deterministic AgentReview quality gate is Phase 5A of the offline review
engine. It validates the synthesized `final-review.json` against
`chunk-results.json` and writes `review-quality-gate.json`.

```text
final-review.json
+ chunk-results.json
-> aiops-review-quality-gate.py
-> review-quality-gate.json
```

## Safety Boundary

The quality gate runs only as CT104/dev toolrepo AgentReview tooling. It does
not call Agent Router, direct providers, `/v1/chat/ingest`, network services,
GitHub write APIs, CT102, Docker, SSH, deploy, restart, telemetry, or second
opinion services. It does not modify AgentEscala or any target repository.

The CLI fails closed unless the environment declares:

```text
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

## CLI Contract

Required inputs:

```text
python scripts/aiops-review-quality-gate.py \
  --final-review /path/to/final-review.json \
  --chunk-results /path/to/chunk-results.json \
  --output /path/to/review-quality-gate.json
```

Optional inputs:

```text
  --intake /path/to/aiops-intake.json \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --redaction-report /path/to/redaction-report.json \
  --checks /path/to/checks.json \
  --critical-pr
```

Invalid required JSON/schema input exits `1` and writes no output. Optional
inputs may be absent; if an optional input is provided but invalid, the CLI also
exits `1` and writes no output. The output path must not equal any input path
and must not be inside a declared target repository root.

## Output

The output schema is `agent-review.quality-gate.v1`:

```json
{
  "schema_version": 1,
  "schema_id": "agent-review.quality-gate.v1",
  "source": "aiops-review-quality-gate",
  "status": "passed",
  "normalized_verdict": "approved",
  "quality_score": 1.0,
  "manual_review_required": false,
  "second_opinion_requested": false,
  "second_opinion_status": "not_required",
  "blocked_reasons": [],
  "warnings": [],
  "limitations": [],
  "inputs": {},
  "created_at": "2026-06-02T00:00:00Z"
}
```

`quality_score` is diagnostic only. Merge decision signals are `status`,
`normalized_verdict`, and `manual_review_required`.

## Consumer contract

`review-quality-gate.json` is the canonical post-synthesis signal for a future
AgentEscala thin wrapper. The wrapper must validate `schema_id`,
`schema_version`, `source`, and the gate enum values before publishing any
conclusive or gate-derived comment/summary. It must consume `status`, `normalized_verdict`,
`manual_review_required`, `blocked_reasons`, `warnings`, and `limitations`
without recalculating or locally reinterpreting the gate.

For toolrepo checkout in the same wrapper flow, `AIOPS_ORCHESTRATOR_SHA` must be
canonical lowercase and match `^[0-9a-f]{40}$`. The wrapper should validate:

```text
[[ "$AIOPS_ORCHESTRATOR_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git -C "$RUNNER_TEMP/aiops-orchestrator" rev-parse HEAD)" \
  = "$AIOPS_ORCHESTRATOR_SHA"
```

Allowed consumption matrix (validated before consuming any gate field as
authority):

| status | normalized_verdict | manual_review_required | result |
| --- | --- | --- | --- |
| passed | approved / approve_with_minor_notes / approve_with_required_followup | false | conclusive publication |
| passed | changes_requested | false | conclusive blocking publication |
| degraded | changes_requested | false | conclusive blocking publication with disclosed `limitations` |
| Valid gate: manual_review_required | Valid gate: manual_review_required | true | non-conclusive manual-review publication |
| Valid gate: failed | Valid gate: review_unavailable | true | non-conclusive review-unavailable publication |
| any other combination | any | any | `gate_combination_invalid` fail-closed publication |

Additional rules:

- `degraded` never approves.
- `changes_requested` requires non-empty `blocked_reasons`.
- `degraded` requires explicit disclosure of `limitations`.
- For validated `degraded + changes_requested`, the gate is authoritative:
  blocker reliability was determined by AIOps before gate emission, and the
  wrapper must not reconfirm blocker evidence from `final-review.json`.
- Gate-combination validation happens before manual-review routing.
- Invalid combinations must never short-circuit to manual-review rows only
  because they contain `manual_review_required=true`.

If validation fails, the wrapper must still publish a conservative fail-closed
fallback generated from local validation failure details, without trusting
fields from the invalid gate, and with deterministic output:

```text
publication_result=review_unavailable
manual_review_required=true
publication_class=fail_closed
reason_code=<sanitized local reason code>
```

Supported local reason codes include `gate_missing`, `gate_json_invalid`,
`gate_schema_invalid`, `gate_source_invalid`, `gate_version_unsupported`,
`gate_status_unknown`, `gate_verdict_unknown`, `gate_combination_invalid`,
`gate_validation_failed`, `toolrepo_sha_invalid`, `toolrepo_checkout_failed`,
and `toolrepo_sha_mismatch`.

The wrapper must never use `final-review.json` as a replacement authority,
call CT102, use `/v1/chat/ingest`, or apply
`suggested-contract-updates.yaml`.

## Deterministic Rules

- Unknown `final-review.verdict` in an otherwise readable final review produces
  `status=failed`, `normalized_verdict=review_unavailable`,
  `manual_review_required=true`, and `final_review_verdict_unknown`.
- Structurally invalid JSON or schema is not normalized; it is a CLI failure
  with no output.
- P0/P1 findings can normalize to `changes_requested` only when they have file
  path, impact, concrete evidence, source artifact or line/hunk, and a parsed
  source chunk.
- If `source_chunks` is absent, the gate may fall back to `chunk_id`; unparsed
  `source_chunks` are not reliable.
- Empty, redacted, placeholder, or truncation-only evidence cannot support a
  blocker.
- Without a reliable P0/P1 blocker, partial/degraded/failed inputs or critical
  coverage gaps require manual review and never normalize to
  `changes_requested`.
- Test failure claims are checked only for source/evidence presence in this PR;
  `checks.json` is not interpreted deeply.
- CT102, production, deploy, restart, and runtime claims use simple keyword
  checks. They only support blockers with trusted source artifacts and explicit
  operational evidence.
- `second_opinion_requested` is always `false` and
  `second_opinion_status` is always `not_required` in this phase.

## Critical PR Coverage

With `--critical-pr`, clear coverage gaps require manual review. The gate checks
final review coverage, missing expected files, chunk-plan uncovered files, and
best-effort `coverage_requirements.must_review_files` from known sanitized
intake artifact shapes. Missing optional coverage metadata does not fail the
gate by itself.
