# AgentEscala Tool Repo Integration

Phase 05 integrates `mglpsw/AgentEscala` with the AgentReview Engine in
`mglpsw/aiops-orchestrator` as a pinned local tool repo on CT104.

## Boundary

AgentReview for AgentEscala runs only on CT104 as development tooling. It must
not call CT102, use CT102 as staging, deploy, restart services, run SSH, run
Docker remotely, call providers directly, use `/v1/chat/ingest`, auto-approve,
auto-merge, or apply contract suggestions automatically.

The AIOps tool repo remains a deterministic offline engine through Phase 04. In
Phase 05, AgentEscala owns orchestration and any optional LLM calls. AgentEscala
may call Agent Router only through the OpenAI-compatible endpoint
`/v1/chat/completions`.

## Ownership

`aiops-orchestrator` owns:

- intake, redaction, semantic chunk planning, structured chunk result parsing,
  and final deterministic synthesis;
- public schemas for `aiops-intake.json`, `semantic-chunk-plan.json`,
  `chunk-results.json`, and `final-review.json`;
- contract docs and offline contract tests.

`AgentEscala` owns:

- product-specific artifact generation;
- `.aiops/domain-contracts.yaml` and `.aiops/review-packs.yaml`;
- workflow orchestration in GitHub Actions on CT104;
- optional Agent Router calls for each chunk;
- PR comment publication from `final-review.md`;
- temporary manual fallback to the legacy review path.

Product rules stay in AgentEscala. Generic review engine logic stays in AIOps.

## Required Target Profile

AgentEscala must declare only relative artifact paths under the workflow
artifact directory passed to `aiops-review-intake.py --agent-dir`. A valid
`.aiops/repo-profile.yaml` for Phase 05 is:

```yaml
schema_version: agent-review.target-profile.v1
target_repo: mglpsw/AgentEscala
name: AgentEscala

artifacts:
  - name: checks
    path: checks.json
    kind: json
    required: true

  - name: file-diff-context
    path: file-diff-context.json
    kind: json
    required: true

  - name: full-diff
    path: full.diff
    kind: diff
    required: false

  - name: test-intelligence
    path: test-intelligence.json
    kind: json
    required: false

  - name: project-context
    path: project-context.json
    kind: json
    required: false

  - name: semantic-context
    path: semantic-context.json
    kind: json
    required: false

  - name: local-code-intelligence
    path: local-code-intelligence.json
    kind: json
    required: false

  - name: validation-evidence-result
    path: validation-evidence/validation-evidence-result.json
    kind: json
    required: false
```

`domain-contracts.yaml` and `review-packs.yaml` remain in AgentEscala and are
loaded by the AIOps intake into sanitized target profile metadata.

## CT104 Directory Contract

The AgentEscala workflow should use separate local directories:

```text
$GITHUB_WORKSPACE                 AgentEscala checkout
$RUNNER_TEMP/aiops-orchestrator   pinned AIOps tool repo checkout
$RUNNER_TEMP/agent                generated AgentEscala artifacts and AIOps outputs
$RUNNER_TEMP/agent/chunk-responses structured responses consumed by Phase 3
```

The required final outputs are:

```text
$RUNNER_TEMP/agent/aiops-intake.json
$RUNNER_TEMP/agent/redaction-report.json
$RUNNER_TEMP/agent/semantic-chunk-plan.json
$RUNNER_TEMP/agent/chunk-results.json
$RUNNER_TEMP/agent/final-review.json
$RUNNER_TEMP/agent/final-review.md
```

The AIOps CLIs reject outputs inside the target repository root. Keep all
generated review outputs in `$RUNNER_TEMP/agent`.

## Chunk Response Contract

For each chunk in `semantic-chunk-plan.json`, AgentEscala writes exactly:

```text
$RUNNER_TEMP/agent/chunk-responses/<chunk_id>.json
```

The minimum schema is:

```json
{
  "schema_version": 1,
  "chunk_id": "chunk-01-primary_backend_logic",
  "semantic_group": "primary_backend_logic",
  "confirmed_findings": [],
  "risks": [],
  "limitations": [],
  "coverage_notes": {
    "files_reviewed": [],
    "files_partial": [],
    "files_not_reviewed": []
  }
}
```

Confirmed findings must include concrete evidence: severity, title, file path,
impact, and either source artifact or line/hunk context. Findings without that
evidence are downgraded or rejected by the parser.

When LLM review is disabled, AgentEscala may emit an empty structured response
per chunk with limitation `llm_disabled`. When Agent Router fails, AgentEscala
may either omit the expected chunk response or emit an empty degraded response.
It must never invent findings.

## Pinning

AgentEscala should pin the AIOps tool repo by full commit SHA. The
`v0.19.0-rc.1` tag is a human release label created manually after the AIOps
Phase 05A PR merges; the workflow should validate that `git rev-parse HEAD`
matches the configured SHA.

