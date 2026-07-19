# AgentReview PR Brief and Chunk Payloads

`v0.20.0` delivers deterministic, sanitized, provider-neutral build artifacts
for target-repository review orchestration:

- `pr-brief.json` (`agent-review.pr-brief.v1`);
- `chunk-payload-manifest.json` (`agent-review.chunk-payload-manifest.v1`);
- one payload file per planned chunk in `chunk-payloads/`
  (`agent-review.chunk-payload.v1`).

## Boundary

This stage does **not** call models, providers, Agent Router, or GitHub write
APIs. It performs no runtime network calls and is CT104/toolrepo-only.

The following are explicitly prohibited:

- CT102 usage;
- `/v1/chat/ingest`;
- direct provider calls (OpenAI/Anthropic/Ollama);
- raw prompt/response emission.

## Inputs and outputs

Required inputs:

- `aiops-intake.json`;
- `semantic-chunk-plan.json`;
- `redaction-report.json`.

Optional inputs:

- `checks.json`;
- `validation-evidence-result.json`.

Missing optional inputs produce explicit limitations. Required invalid inputs
fail closed without partial outputs.

## Determinism and sanitization

For identical inputs, outputs are byte-identical (stable sorting, canonical
JSON, deterministic hashing). Artifacts are sanitized before hashing/writing
to remove secrets and local absolute paths.

Truncation is explicit via metadata:

- `original_chars`;
- `emitted_chars`;
- `omitted_sections`;
- `truncation_reason`;
- `coverage_impact`.

## AgentEscala integration

These artifacts are preparatory outputs for the future thin-wrapper
implementation tracked in `mglpsw/AgentEscala#670`. AgentEscala remains the
owner of orchestration and the future `/v1/chat/completions` call, while AIOps
remains owner of context selection, payload schema, sanitization, and
deterministic limits.
