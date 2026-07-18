# Release v0.20.0

## 1. Release identity

- Release: `v0.20.0` — AgentReview Quality Gate track.
- Baseline `master`: `c3132b26cdd5db1ab29efb733671959fd803f9c2`.
- Release SHA: pending the squash merge of the release PR into `master`.
- Previous release and rollback ref: `v0.19.0`.
- The only runtime-facing change in the release PR is the reported version
  default from `0.19.0` to `0.20.0`; no runtime logic is changed.

## 2. Included capabilities

### Deterministic quality gate and E2E

- A deterministic post-synthesis quality gate validates `final-review.json`
  against `chunk-results.json` and emits `review-quality-gate.json`.
- The gate is the canonical decision authority. It normalizes approved,
  blocking, degraded, manual-review, and unavailable outcomes without a second
  provider opinion.
- The offline E2E contract covers intake, semantic chunk planning, deterministic
  PR brief and bounded chunk payload construction, structured chunk parsing,
  synthesis, quality gate, telemetry, and optional false-positive artifacts.
- E2E validation proves deterministic output, schema compatibility,
  fail-closed production/runtime rejection, and no target-repository writes.

### Review telemetry and false-positive learning

- `review-telemetry.json` observes the final review and quality gate without
  changing the normalized verdict or persisting telemetry in a database.
- `false-positive-signatures.json` records deterministic signatures derived
  from confirmed findings and optional human-authored markers.
- `suggested-contract-updates.yaml` is human-reviewable and strictly
  `manual_only`; `applied` remains `false`. No suggestion is applied
  automatically.

### AgentEscala thin-wrapper contract

- AgentEscala remains the target-repository thin wrapper. It owns orchestration
  and GitHub publication, consumes the validated quality gate, and does not
  reimplement parsing, synthesis, gate, telemetry, or false-positive logic.
- Operational checkout of this toolrepo is pinned to a canonical lowercase
  full 40-character commit SHA. Tags may be used only by a maintainer to select
  and verify that SHA; the workflow never resolves a floating tag or branch.
- Missing, malformed, incompatible, unknown, or contradictory gates publish a
  deterministic fail-closed fallback and never treat `final-review.json` as a
  substitute authority.

### Deterministic review context and chunk response contract

- `pr-brief.json` is the deterministic, sanitized summary used to construct
  review context.
- The review-context manifest is concretely published as
  `chunk-payload-manifest.json`; it records the bounded payload set and hashes.
- Each `chunk-payloads/<chunk_id>.json` contains bounded, isolated, sanitized
  context for one semantic chunk, with deterministic size accounting and
  explicit truncation/limitation metadata.
- Every chunk payload contains a complete structured response contract,
  including nested shapes for findings, risks, limitations, coverage, and a
  chunk-specific minimum valid template. Responses are JSON objects only.

### PR #77 corrections

PR #77 closes the post-merge payload-contract gaps from PR #76:

- preserves `validation_risks` and `facts_for_synthesizer` with deterministic
  file, global, and unscoped routing;
- gives explicit global scope precedence over path provenance while retaining
  sanitized provenance;
- rejects chunk IDs that are incompatible with artifacts or response files;
- centralizes the complete structured per-chunk response contract; and
- retains deterministic ordering, isolation, redaction, and fail-closed output
  behavior, with unit, CLI, and offline E2E regressions.

## 3. Public artifacts and schemas

| Artifact | Schema or format |
| --- | --- |
| `aiops-intake.json` | `agent-review.intake.v1` (legacy envelope: string `schema_version`, no `schema_id`) |
| `redaction-report.json` | `agent-review.redaction-report.v1` (legacy envelope: string `schema_version`, no `schema_id`) |
| `semantic-chunk-plan.json` | `agent-review.semantic-chunk-plan.v1` |
| `pr-brief.json` | `agent-review.pr-brief.v1` |
| `chunk-payload-manifest.json` | `agent-review.chunk-payload-manifest.v1` |
| `chunk-payloads/<chunk_id>.json` | `agent-review.chunk-payload.v1` |
| `chunk-results.json` | `agent-review.chunk-results.v1` |
| `final-review.json` | `agent-review.final-review.v1` |
| `final-review.md` | sanitized Markdown |
| `review-quality-gate.json` | `agent-review.quality-gate.v1` |
| `review-telemetry.json` | `agent-review.telemetry.v1` |
| `false-positive-markers.json` | `agent-review.false-positive-markers.v1` (optional manual input) |
| `false-positive-signatures.json` | `agent-review.false-positive-signatures.v1` |
| `suggested-contract-updates.yaml` | `agent-review.contract-suggestions.v1` (`manual_only`, `applied: false`) |

Consumers must validate each artifact against its emitted schema envelope.
`aiops-intake.json` and `redaction-report.json` use the legacy string
`schema_version` identifier and omit `schema_id`; the other versioned JSON/YAML
artifacts above use a separate `schema_id` and integer `schema_version`.
Consumers must also validate source, enumerations, and allowed combinations
where defined before treating an artifact as authoritative.

## 4. Compatibility and migrations

- No database, migration, route, provider, action-catalog, or API behavior
  change is included in the release PR.
- No data migration is required.
- Existing runtime configuration remains compatible. The default reported app
  version changes to `0.20.0` after a separately approved controlled deploy.
- AgentEscala consumption must follow the full-SHA pin and quality-gate
  validation contract; it must not use a branch, tag, or short SHA as the
  operational checkout ref.

## 5. Environment and safety boundaries

- CT104 remains the development toolrepo and AgentReview runner environment.
- CT102 remains the production AIOps runtime. It is not staging, and
  AgentReview tooling never runs there.
- This release preparation makes no call to CT102 and performs no deploy,
  restart, SSH, Docker operation, or service-manager action.
- AgentReview tooling makes no call to `/v1/chat/ingest`.
- AgentReview tooling makes no direct OpenAI, Anthropic, Ollama, or other
  provider call. Optional model requests are owned by the thin-wrapper flow and
  use the approved Agent Router `/v1/chat/completions` contract.
- No contract suggestion is applied automatically.
- Secrets, raw prompts, raw provider payloads, headers, cookies, tokens, and
  local absolute paths are forbidden in uploaded artifacts. The publication
  allowlist is conditional: the thin wrapper must sanitize and scan each
  artifact at publication time and omit any artifact that fails. In particular,
  a successful `aiops-intake.json` generation does not by itself prove that
  declared artifact content is free of local absolute paths.

## 6. Explicitly out of scope

- A real or optional second-opinion implementation.
- Validation Evidence semantic pre-review.
- AgentEscala code changes.
- Runtime refactors, provider changes, routes, database changes, migrations,
  deploy automation, auto-approve, auto-merge, or automated remediation.

## 7. Release sequence

The required sequence is:

```text
release PR
-> merge into master
-> tag v0.20.0-rc.1
-> controlled deploy on CT102
-> postchecks
-> final tag/release v0.20.0 on the same SHA
```

The release PR must be small, validated, CI-green, free of open P0/P1/P2
findings and unresolved review threads, and independently approved by a human
on its final SHA before merge.

## 8. Release-candidate criteria

Create signed tag `v0.20.0-rc.1` only after:

- the release PR is merged into `master` through the protected squash-merge
  path;
- the merged SHA contains only the validated release/versioning diff;
- the full offline test suite and repository CI validation are green;
- all review threads are resolved and no P0/P1/P2 finding remains open; and
- an independent human approval applies to the final PR SHA.

The RC GitHub release is a prerelease targeted at that exact merged SHA. If GPG
signing is unavailable, stop and obtain an explicit decision; do not create an
unsigned replacement implicitly.

## 9. Controlled deploy and final release criteria

Deploy is a separate, explicitly authorized CT102 operation after RC creation.
The final `v0.20.0` tag and GitHub release may be created only when:

- the controlled CT102 change window, operator, reviewer, backup/snapshot, and
  rollback target are confirmed;
- CT102 reports the expected `0.20.0` runtime version at the RC SHA;
- health, readiness, metrics, action catalog, database, provider registry,
  audit store, approval store, and run store postchecks pass;
- critical logs and preservation evidence show no release-blocking issue;
- rollback remains possible and all required evidence is reviewed; and
- the final `v0.20.0` tag targets the same SHA as `v0.20.0-rc.1`.

Do not create the final tag during release-PR preparation or RC publication.

## 10. Rollback

- Current rollback ref: annotated final release tag `v0.19.0`
  (`9c90eac6205782a17a1567737aef026728f88089`).
- Resolve and verify the rollback tag and commit before the CT102 change window.
- Preserve the database/volume, `config/`, `.env` without exposing secrets,
  `var/audit`, `var/approvals`, and `var/runs` according to the existing CT102
  backup/rollback contract.
- Roll back if health, readiness, metrics, action catalog, persistent stores,
  provider state, version, or critical logs do not meet the approved postcheck
  criteria.
