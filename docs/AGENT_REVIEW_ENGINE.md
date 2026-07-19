# AgentReview Engine

AgentReview Engine is the offline deterministic review engine delivered in the
`v0.20.0` line of `aiops-orchestrator`. Its implemented pipeline covers intake
and redaction, semantic chunk planning, deterministic PR brief and bounded
chunk payload construction, structured chunk result parsing, final synthesis,
post-synthesis quality gate, telemetry and optional false-positive artifacts.

The numbered phases below describe component boundaries, not unfinished release
work. Target-repository orchestration and any optional model call remain outside
the AIOps CLIs.

## Runtime Boundary

The engine runs on CT104 as local toolrepo work only. It is not CT102 runtime
behavior and does not add FastAPI endpoints, Agent Router calls, provider calls,
network access, Docker, SSH, deploy, restart, or operational command execution.

Every AgentReview CLI validates the environment boundary before processing
artifacts and keeps the same fail-closed guard pattern.

## Offline Intake

The CLI reads:

```text
.aiops/repo-profile.yaml
.aiops/domain-contracts.yaml
.aiops/review-packs.yaml
```

from the target repository root, then loads only artifacts declared by the
profile from the provided agent artifacts directory.

Example:

```text
python scripts/aiops-review-intake.py \
  --target-repo mglpsw/AgentEscala \
  --repo-root /path/to/target/repo \
  --agent-dir /path/to/agent/artifacts \
  --output /path/to/aiops-intake.json \
  --redaction-report /path/to/redaction-report.json
```

Outputs:

- `aiops-intake.json` with target profile, artifact status, sanitized artifacts,
  redaction summary, limitations, completeness, and status.
- `redaction-report.json` with deterministic replacement counts and safety
  metadata.

The CLI rejects output paths inside `--repo-root` so the target repo is not
modified.

## Safety Limits

Phase 1 does not publish raw prompts or raw artifact content. Artifact content is
sanitized before it enters the intake object. The redaction report never includes
original secret values.

This phase also does not apply contract fixes, generate findings, classify
severity, call an LLM, call Agent Router, or modify AgentEscala.

## Deterministic PR brief and bounded chunk payloads

`scripts/aiops-review-build-payloads.py` reads sanitized intake/chunk-plan
artifacts and writes:

- `pr-brief.json` (`agent-review.pr-brief.v1`);
- `chunk-payload-manifest.json` (`agent-review.chunk-payload-manifest.v1`);
- one payload per chunk in `chunk-payloads/`
  (`agent-review.chunk-payload.v1`).

This stage is deterministic, provider-neutral, and sanitized. It declares
explicit truncation and coverage impact metadata and does not call models,
Agent Router, `/v1/chat/ingest`, direct providers, network, CT102, or GitHub
write APIs.

## Semantic Chunk Planning

`scripts/aiops-review-plan-chunks.py` reads only sanitized `aiops-intake.json`
from Phase 1 and writes a `semantic-chunk-plan.json` file.

Example:

```text
python scripts/aiops-review-plan-chunks.py \
  --intake /path/to/aiops-intake.json \
  --output /path/to/semantic-chunk-plan.json \
  --max-blocks 6
```

The planner groups changed files into deterministic semantic review roles such
as backend logic, API/schema contracts, frontend UI, tests, workflow/AIOps,
docs/changelog, suspicious out-of-scope, and unknown. It tracks covered,
partially covered, and uncovered files without reading the target repository or
raw artifacts.

Phase 2 does not generate prompts, findings, severity, recommendations, parser
output, final synthesis, quality gates, telemetry, or LLM calls.

## Structured Chunk Result Parsing

`scripts/aiops-review-parse-chunks.py` reads the Phase 2
`semantic-chunk-plan.json` and one structured JSON response per chunk from a
local responses directory:

```text
python scripts/aiops-review-parse-chunks.py \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --responses-dir /path/to/chunk-responses \
  --output /path/to/chunk-results.json
```

An optional sanitized intake file can be provided when the parser should also
enforce known target repository output path boundaries:

```text
python scripts/aiops-review-parse-chunks.py \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --responses-dir /path/to/chunk-responses \
  --intake /path/to/aiops-intake.json \
  --output /path/to/chunk-results.json
```

For each semantic chunk, the parser reads only:

```text
<responses-dir>/<chunk_id>.json
```

It does not recurse through the responses directory, and extra files are
ignored. Missing or invalid responses are recorded as chunk parse failures.

The parser writes `chunk-results.json` with schema
`agent-review.chunk-results.v1`. The output separates confirmed findings,
risks, limitations, rejected findings, coverage, parsed chunks, failed chunks,
and parser status. `chunk_plan_ref` contains schema/status/count metadata only;
it does not include local absolute paths.

Confirmed findings remain confirmed only when they include concrete evidence,
file path, title, impact, and either source artifact or line/hunk context.
Findings without concrete evidence, speculative language, unsupported test
failure sources, or placeholder-only evidence are downgraded to risks or
rejected. Findings outside their semantic chunk are rejected with
`file_not_in_chunk`. The parser never creates new findings, invents lines or
contracts, raises severity, applies fixes, or produces a final PR verdict.

Phase 3 still does not call an LLM, Agent Router, providers, network, CT102,
FastAPI runtime, Docker, SSH, deploy/restart commands, final synthesis, quality
gates, or telemetry.

## Final Review Synthesis

`scripts/aiops-review-synthesize.py` reads the Phase 3 `chunk-results.json` and
writes a deterministic final review JSON artifact plus a concise pt-BR Markdown
summary:

```text
python scripts/aiops-review-synthesize.py \
  --chunk-results /path/to/chunk-results.json \
  --output-json /path/to/final-review.json \
  --output-md /path/to/final-review.md
```

Optional sanitized context can be provided explicitly:

```text
python scripts/aiops-review-synthesize.py \
  --chunk-results /path/to/chunk-results.json \
  --intake /path/to/aiops-intake.json \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --redaction-report /path/to/redaction-report.json \
  --output-json /path/to/final-review.json \
  --output-md /path/to/final-review.md \
  --max-findings 10 \
  --max-risks 10
```

The synthesizer writes `final-review.json` with schema
`agent-review.final-review.v1` and `final-review.md` for human review. It
deduplicates and orders already-normalized findings and risks, summarizes
rejected findings, consolidates coverage/counts/limitations, and emits a
deterministic preliminary verdict.

Phase 4 treats `chunk-results.json` as the only required source of truth. The
optional intake, chunk plan, and redaction report are used only for metadata,
coverage comparison, limitations, and target repository output path guards. The
synthesizer does not read target repository files directly.

The Phase 4 verdict is preliminary and is not a quality gate. It preserves
finding severity and review boundaries: risks, limitations, and rejected
findings are not promoted into confirmed findings, and no new findings,
evidence, files, lines, contracts, fixes, or recommendations are invented.

Phase 4 still does not call an LLM, Agent Router, providers, network, CT102,
FastAPI runtime, Docker, SSH, deploy/restart commands, quality gates,
telemetry, or second opinion services.

## Deterministic Quality Gate

`scripts/aiops-review-quality-gate.py` reads `final-review.json` and
`chunk-results.json`, then writes `review-quality-gate.json`:

```text
python scripts/aiops-review-quality-gate.py \
  --final-review /path/to/final-review.json \
  --chunk-results /path/to/chunk-results.json \
  --output /path/to/review-quality-gate.json
```

Optional sanitized context can be provided for path guards and coverage checks:

```text
python scripts/aiops-review-quality-gate.py \
  --final-review /path/to/final-review.json \
  --chunk-results /path/to/chunk-results.json \
  --intake /path/to/aiops-intake.json \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --redaction-report /path/to/redaction-report.json \
  --checks /path/to/checks.json \
  --critical-pr \
  --output /path/to/review-quality-gate.json
```

The gate writes schema `agent-review.quality-gate.v1`. It validates whether the
final verdict is supported by reliable deterministic evidence, normalizes unsafe
verdicts, records warnings and limitations, and emits a diagnostic
`quality_score`. The score is not a merge criterion; downstream decisions should
use `status`, `normalized_verdict`, and `manual_review_required`.

Phase 5A distinguishes structural input failure from verdict normalization:
invalid JSON/schema exits without output, while an unknown but structurally
readable final-review verdict produces a failed gate artifact with
`normalized_verdict=review_unavailable`.

This phase still does not call an LLM, Agent Router, providers, network, CT102,
FastAPI runtime, Docker, SSH, deploy/restart commands, second opinion services,
GitHub write APIs, or AgentEscala code.

## Review Telemetry

`scripts/aiops-review-telemetry.py` reads `final-review.json` and
`review-quality-gate.json`, then writes `review-telemetry.json`:

```text
python scripts/aiops-review-telemetry.py \
  --final-review /path/to/final-review.json \
  --quality-gate /path/to/review-quality-gate.json \
  --output /path/to/review-telemetry.json
```

Optional sanitized artifacts can be provided for richer metrics:

```text
python scripts/aiops-review-telemetry.py \
  --final-review /path/to/final-review.json \
  --quality-gate /path/to/review-quality-gate.json \
  --chunk-results /path/to/chunk-results.json \
  --chunk-plan /path/to/semantic-chunk-plan.json \
  --intake /path/to/aiops-intake.json \
  --redaction-report /path/to/redaction-report.json \
  --checks /path/to/checks.json \
  --output /path/to/review-telemetry.json
```

Telemetry writes schema `agent-review.telemetry.v1`. It observes target and
pipeline metrics, coverage counts, finding/risk/rejected counts, final review
status, quality gate status, normalized verdict, manual-review requirement,
redaction status, optional validation evidence status, and already-reported
performance metadata. Missing optional artifacts become limitations, not opaque
failures.

Phase 5B still does not decide, recalibrate severity, confirm findings, apply
contracts, call an LLM, call Agent Router, use `/v1/chat/ingest`, write target
repository files, persist telemetry to a database, or alter AgentEscala.

## Delivered scope and follow-ups

`v0.20.0` implements local intake/redaction, semantic chunk planning,
deterministic PR brief and bounded payload building, structured chunk result
parsing, final deterministic synthesis, quality gate, telemetry and optional
false-positive artifacts.

The target-repository contract integrates this offline engine with AgentEscala
as a CT104 thin wrapper.
AgentEscala remains responsible for product artifact generation, optional Agent
Router calls through `/v1/chat/completions`, and PR comment publication. The
AIOps tool repo remains deterministic and does not call LLMs, providers, GitHub
APIs, CT102, Docker, SSH, deploy, restart, or operational commands. The offline
AIOps E2E contract now covers the quality-gate artifact through
`review-quality-gate.json`; second opinion and AIOps-owned LLM block running
remain future work. The contract suite also fails closed when forced into
production/runtime environment flags and asserts that both the copied target
fixture and source fixture remain unchanged throughout execution.
