# AgentReview v2 contract foundation

Issue #80 introduces an explicitly versioned contract line alongside the
operational AgentReview v1 pipeline from `v0.20.0`. This first delivery freezes
only the v2 data models and JSON Schemas. It does not activate v2 in any CLI,
planner, payload builder, parser, synthesizer, quality gate, Router endpoint, or
target-repository workflow.

## Frozen contracts

| Contract | Purpose |
| --- | --- |
| `agent-review.run.v2` | Deterministic run identity and explicit origin/lifetime metadata |
| `agent-review.chunk-payload.v2` | Run-bound chunk identity, coverage, and typed artifact/contract references |
| `agent-review.chunk-response-envelope.v2` | Discriminated success/error response bound to run, chunk, payload, and HEAD |
| `agent-review.target-profile.v2` | Strict generic target identity, artifacts, budgets, must-review policy, policies, and contracts |
| `agent-review.review-readiness.v2` | Readiness state, stable reasons, blockers, and finding lifecycle |

The Python authority is `app/agent_review/contracts_v2.py`. Every contractual
object uses Pydantic 2 strict validation, freezes instances, and rejects unknown
fields at every nesting level. Schema ID, schema version, and source are
required constants. Git commit SHAs are canonical lowercase 40-character
hexadecimal values; SHA-256 values are canonical lowercase 64-character
hexadecimal values. Relative paths reject absolute paths, Windows paths, home
paths, and parent traversal.

The committed JSON Schemas live under `schemas/agent-review/v2/`. They are
derived directly from the Pydantic validation schemas, and every object has
`additionalProperties: false`. Regenerate them with:

```bash
python3 scripts/export-agent-review-v2-schemas.py
```

## Canonical run identity bytes

`run_id` is the lowercase SHA-256 hex digest of exactly one UTF-8 byte sequence.
That sequence is the JSON serialization of these nine fields and no others:

```text
repo
pr_number
base_sha
head_sha
tested_merge_sha
toolrepo_sha
profile_hash
policy_hash
evidence_hash
```

Serialization uses Python `json.dumps` with:

```text
ensure_ascii=False
sort_keys=True
separators=(",", ":")
allow_nan=False
```

The resulting string is encoded with UTF-8 and hashed directly. There is no
trailing newline and no delimiter concatenation. Dictionary insertion order,
clock values, UUIDs, randomness, local paths, and implicit timestamps never
participate. `created_at`, `expires_at`, and origin metadata in the run envelope
must be supplied explicitly and do not change `run_id`.

The golden fixture in
`tests/agent_review/fixtures/v2/golden_run_identity.json` freezes both the exact
JSON text and digest.

## Payload and response binding

The v2 chunk payload carries the complete run identity, `run_id`, `chunk_id`,
semantic group, `payload_sha256`, typed coverage, and typed artifact/contract
references. Its `run_id` must match the canonical identity before the model is
accepted.

The response envelope is a discriminated union on `status`:

- `success` requires a typed sanitized result and forbids an error object;
- `error` requires only a closed reason-code enum plus `retryable`, and forbids
  a result object or free-form error payload.

Both variants require `run_id`, `chunk_id`, `payload_sha256`, `head_sha`,
provider, model, attempt, request ID, finish reason, and `response_sha256`.
`validate_response_binding_v2` detects run, chunk, payload, and HEAD divergence
before a future parser inspects findings. Connecting that check to the parser is
reserved for PR 2 of #80.

## Target profile and readiness

`TargetProfileV2` is repository-neutral. It contains no branches for
AgentEscala, InterLeitos, or any other target. This delivery intentionally does
not replace the v1 profile loader; the full loader/migrator belongs to PR 4.

Readiness states are `ready`, `blocked_code`, `blocked_pipeline`,
`manual_required`, and `stale`. Stable reason categories are
`schema_failure`, `transport_failure`, `coverage_failure`, `policy_failure`,
`model_uncertainty`, and `confirmed_code_finding`. Finding dispositions are
`new`, `confirmed`, `fixed`, `dismissed`, `superseded`, and `stale`.

Structural validation rejects contradictory combinations. In particular:

- `ready` has no reason codes, active blockers, or actionable new/confirmed
  findings;
- blocked/manual states have non-empty reason codes matching active blockers;
- code blockers require a confirmed actionable finding;
- operational failures remain `blocked_pipeline` rather than model approval;
- `stale` is the only state allowed to bind a different evaluated HEAD;
- dismissal requires justification, a responsible identity, and commit/test
  evidence.

This contract does not yet calculate readiness in the v1 quality gate.

## Compatibility window

The `v0.20.0` v1 models, JSON artifacts, CLIs, quality gate, and target wrapper
contract remain operational and unchanged. During the #80 migration window,
v1 and v2 are separate contract lines; consumers must select a version
explicitly and must never mix envelopes silently.

PR 2 will add fail-closed binding to consumers, PR 3 will add deterministic
multi-chunk planning by change unit, and PR 4 will complete profile loading and
the isolated toolrepo environment. Until those deliveries adopt v2 explicitly,
the exported v2 schemas are development contracts only and do not represent a
new release or production version.
