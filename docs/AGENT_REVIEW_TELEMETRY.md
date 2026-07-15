# AgentReview Telemetry

The AgentReview telemetry baseline observes existing offline artifacts after the
deterministic quality gate and writes `review-telemetry.json`.

```text
final-review.json
+ review-quality-gate.json
+ optional pipeline artifacts
-> aiops-review-telemetry.py
-> review-telemetry.json
```

## Safety Boundary

Telemetry runs only as CT104/dev toolrepo AgentReview tooling. It does not call
LLMs, Agent Router, direct providers, `/v1/chat/ingest`, network services,
GitHub write APIs, CT102, Docker, SSH, deploy, restart, second opinion services,
or AgentEscala code. It does not modify the target repository.

## CLI Contract

Required inputs:

```text
python scripts/aiops-review-telemetry.py \
  --final-review /path/to/final-review.json \
  --quality-gate /path/to/review-quality-gate.json \
  --output /path/to/review-telemetry.json
```

Optional inputs:

```text
  --chunk-results /path/to/chunk-results.json \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --intake /path/to/aiops-intake.json \
  --redaction-report /path/to/redaction-report.json \
  --checks /path/to/checks.json \
  --validation-evidence /path/to/validation-evidence-result.json \
  --test-intelligence /path/to/test-intelligence.json \
  --local-code-intelligence /path/to/local-code-intelligence.json \
  --pr-number 61 \
  --commit-sha abc123 \
  --review-mode offline \
  --contract-pack phase05 \
  --critical-pr
```

Invalid or missing required inputs exit with no output. Missing or invalid
optional artifacts are represented as structured limitations so the telemetry
artifact remains deterministic and audit-friendly.

## Output

The output schema is `agent-review.telemetry.v1`:

```json
{
  "schema_version": 1,
  "schema_id": "agent-review.telemetry.v1",
  "source": "aiops-review-telemetry",
  "status": "complete",
  "target": {},
  "pipeline": {},
  "coverage": {},
  "findings": {},
  "review": {},
  "quality_gate": {},
  "validation_evidence": {},
  "redaction": {},
  "model": {},
  "performance": {},
  "inputs": {},
  "warnings": [],
  "limitations": []
}
```

`review-quality-gate.json` remains authoritative for the final normalized
verdict. Telemetry copies `quality_gate.normalized_verdict`,
`quality_gate.status`, `quality_gate.manual_review_required`, and gate blocked
reasons without recalibrating or applying a new decision.

## Determinism and Redaction

The generator does not create timestamps, random IDs, hostnames, or wall-clock
durations. It only transports durations, bundle sizes, model/provider/preset
metadata, PR numbers, and commit SHAs when they already exist in inputs or are
provided explicitly by CLI arguments.

Telemetry output is sanitized with the shared AgentReview redaction helpers and
redacts secrets, headers, env values, raw payload-style values, and local
absolute paths.
