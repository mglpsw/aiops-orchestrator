# AgentReview Engine

AgentReview Engine is the future generic review engine in `aiops-orchestrator`.
Phase 1 is limited to offline intake and deterministic redaction. Phase 2 adds
deterministic semantic chunk planning over the sanitized intake.

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

## Roadmap

This implements the local intake/redaction and semantic chunk planning
foundation for issue #46. Parser, synthesizer, quality gate, telemetry, and
LLM-backed review remain future work.
