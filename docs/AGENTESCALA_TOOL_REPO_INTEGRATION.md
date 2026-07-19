# AgentEscala Tool Repo Integration

Status: supplemental guidance. The canonical wrapper contract is
`docs/AGENTESCALA_TARGET_REPO_CONTRACT.md`.

Phase 05 defines the integration of `mglpsw/AgentEscala` with the AgentReview
Engine in `mglpsw/aiops-orchestrator` as a pinned local tool repo on CT104.
Target-repository adoption of the v0.20.0 quality gate remains tracked in
`mglpsw/AgentEscala#670`.

## Boundary

AgentReview for AgentEscala runs only on CT104 as development tooling. It must
not call CT102, use CT102 as staging, deploy, restart services, run SSH, run
Docker remotely, call providers directly, use `/v1/chat/ingest`, auto-approve,
auto-merge, or apply contract suggestions automatically.

The AIOps tool repo remains deterministic and offline through the complete
`v0.20.0` pipeline. In Phase 05, AgentEscala owns orchestration and any optional
LLM calls. AgentEscala may call Agent Router only through the OpenAI-compatible
endpoint `/v1/chat/completions`.

## Ownership

`aiops-orchestrator` owns:

- intake, redaction, semantic chunk planning, PR brief, bounded chunk payloads,
  structured chunk result parsing and final deterministic synthesis;
- deterministic quality gate, telemetry, false-positive signatures and
  manual-only contract suggestions;
- public schemas for every artifact documented in `RELEASE_V0_20_0.md`;
- contract docs and offline contract tests.

`AgentEscala` owns:

- product-specific artifact generation;
- `.aiops/domain-contracts.yaml` and `.aiops/review-packs.yaml`;
- workflow orchestration in GitHub Actions on CT104;
- optional Agent Router calls for each chunk;
- publication by consuming `review-quality-gate.json` (conclusive
  `final-review.md` only for valid gate combinations);
- fail-closed fallback when gate validation fails (legacy rollback only when
  explicitly configured by maintainers).

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
$RUNNER_TEMP/agent/pr-brief.json
$RUNNER_TEMP/agent/chunk-payload-manifest.json
$RUNNER_TEMP/agent/chunk-payloads/<chunk_id>.json
$RUNNER_TEMP/agent/chunk-results.json
$RUNNER_TEMP/agent/final-review.json
$RUNNER_TEMP/agent/final-review.md
$RUNNER_TEMP/agent/review-quality-gate.json
$RUNNER_TEMP/agent/review-telemetry.json
```

The AIOps CLIs reject outputs inside the target repository root. Keep all
generated review outputs in `$RUNNER_TEMP/agent`.

Optional/conditional outputs:

```text
$RUNNER_TEMP/agent/false-positive-signatures.json
$RUNNER_TEMP/agent/suggested-contract-updates.yaml
```

`suggested-contract-updates.yaml` is manual-only and must never be
auto-applied.

## Chunk Response Contract

For each chunk in `semantic-chunk-plan.json`, AgentEscala writes exactly:

```text
$RUNNER_TEMP/agent/chunk-responses/<chunk_id>.json
```

Before writing chunk responses, AgentEscala consumes bounded payloads generated
by:

```text
python scripts/aiops-review-build-payloads.py ...
```

This builder is deterministic/offline and does not call models, `/v1/chat/ingest`,
or providers directly. The wrapper can later send payload content only through
Agent Router `/v1/chat/completions` as tracked by `mglpsw/AgentEscala#670`.

Each payload embeds the complete response contract and a chunk-specific minimum
valid response. The following is an illustrative minimal shape; the emitted
payload contract is authoritative:

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

AgentEscala must pin the AIOps tool repo by full commit SHA in canonical
lowercase form:

```text
^[0-9a-f]{40}$
```

Branch refs, tags, short SHA, and floating refs are invalid as runtime checkout
values. Maintainers may select a release tag only in human selection flow
(resolve tag to commit, verify provenance, then store the full SHA in workflow
configuration). Runtime tag resolution is prohibited.

Validation example:

```bash
[[ "$AIOPS_ORCHESTRATOR_SHA" =~ ^[0-9a-f]{40}$ ]]
test "$(git -C "$RUNNER_TEMP/aiops-orchestrator" rev-parse HEAD)" \
  = "$AIOPS_ORCHESTRATOR_SHA"
```

Use `actions/checkout` pinned by verified full commit SHA (never floating action
tags) and `persist-credentials: false` in analysis checkouts.

## Publication behavior

The wrapper validates the gate combination before using any field as authority.

- `status=passed` with approval verdict and `manual_review_required=false`:
  conclusive non-blocking publication.
- `status=passed` with `changes_requested` and
  `manual_review_required=false`: conclusive blocking publication.
- `status=degraded` with `changes_requested` and
  `manual_review_required=false`: conclusive blocking publication only when
  `blocked_reasons` is non-empty and `limitations` are disclosed.
- `status=manual_review_required` or `status=failed`: non-conclusive fallback.
- Any unknown/invalid/contradictory combination: fail closed.

`status=degraded` never approves.

Fail-closed output is deterministic:

```text
publication_result=review_unavailable
manual_review_required=true
publication_class=fail_closed
reason_code=<sanitized local reason code>
```

Never use `final-review.json` as replacement authority and never copy raw
invalid gate payload into comment, summary, or artifact outputs.

Chunk payload request envelopes are temporary sanitized artifacts and should not
be published outside controlled debug/allowlist flows.

## CT104 security constraints

Before self-hosted runner allocation, exclude forks at job level:

```text
github.event.pull_request.head.repo.full_name == github.repository
```

Fork PRs must not access CT104 execution, checkout, artifacts, Agent Router
calls, or secrets.

Also require:

- no `pull_request_target` for untrusted PR code;
- analysis job with read-only permissions where possible;
- publication job separated with minimal write permissions.
