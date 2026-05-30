# AgentReview Engine

AgentReview Engine is the future generic review engine in `aiops-orchestrator`.
Phase 1 is limited to offline intake and deterministic redaction. Phase 2 adds
deterministic semantic chunk planning over the sanitized intake. Phase 3 parses
structured simulated chunk responses into normalized chunk results.

## Runtime Boundary

This phase runs on CT104 as local toolrepo work only. It is not CT102 runtime
behavior and does not add FastAPI endpoints, Agent Router calls, provider calls,
network access, Docker, SSH, deploy, restart, or operational command execution.

`scripts/aiops-review-intake.py` validates the environment boundary before it
loads artifacts. Future AgentReview scripts should keep the same fail-closed
guard pattern.

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

## Roadmap

This implements the local intake/redaction, semantic chunk planning, and
structured chunk result parsing foundation for issue #46. Final synthesizer,
quality gate, telemetry, and LLM-backed review remain future work.
