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
`schema_version`, `source`, and the gate enum values before publishing a
comment or summary. It must consume `status`, `normalized_verdict`,
`manual_review_required`, `blocked_reasons`, `warnings`, and `limitations`
without recalculating or locally reinterpreting the gate.

The wrapper publishes `final-review.md` for a valid `passed` or `degraded`
non-manual gate with
`normalized_verdict` `approved`, `approve_with_minor_notes`,
`approve_with_required_followup`, or `changes_requested`. A `degraded` status
must be disclosed and its limitations included. Any manual-review signal
publishes a conservative `manual_review_required` result; `failed` or
`review_unavailable` publishes `review_unavailable` without presenting a
conclusive final review. A missing, malformed, incompatible, unknown-version,
unknown-status, or contradictory gate fails closed. The complete decision table,
artifact policy, and GitHub publication requirements are in
`AGENTESCALA_TARGET_REPO_CONTRACT.md`.

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
