# AgentReview Engine

AgentReview Engine is the future generic review engine in `aiops-orchestrator`.
Phase 1 is limited to offline intake and deterministic redaction.

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

## Roadmap

This implements the local intake/redaction foundation for issue #46. The next
phase is semantic chunk planning, parser, and synthesizer work. That is
intentionally outside this PR.

