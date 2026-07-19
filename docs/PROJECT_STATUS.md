# AIOps Orchestrator — Project Status

## Canonical checkpoint

The current final release is `v0.20.0`, published on 19 July 2026 from:

```text
13695c73d1da9f16eba5c20e6478e7d51aefbb45
```

The signed `v0.20.0-rc.1` and `v0.20.0` tags resolve to that same commit.
The previous final release and rollback ref is `v0.19.0` at
`9c90eac6205782a17a1567737aef026728f88089`.

## Product surfaces

The repository contains two independent surfaces. Their environment and
authority boundaries must remain explicit.

| Surface | Environment | Purpose | Network/provider behavior |
| --- | --- | --- | --- |
| AIOps runtime | CT102 prod/runtime | diagnose, plan, dry-run, approve and execute fixed read-only actions | uses only approved runtime integrations |
| AgentReview engine | CT104 dev/toolrepo | build and validate deterministic review artifacts | offline CLIs; no direct provider or GitHub write calls |

AgentReview must never run on CT102. CT102 must never be used as a staging
environment for AgentReview.

## Runtime status

The production runtime reports `0.20.0`. Release validation accepted:

- `/health`: HTTP 200 and healthy;
- `/ready`: HTTP 200 and ready;
- `/metrics`: HTTP 200;
- database, providers and action catalog: ready;
- new container: running and healthy;
- restart count: `0`;
- `OOMKilled`: `false`;
- no critical runtime errors;
- previous `0.19.0` image retained for rollback;
- `aiops-orchestrator-next` unchanged.

There was no database migration, route change, provider change, action-catalog
change or runtime API behavior change in `v0.20.0`. The runtime-facing change
was the reported application version.

## Runtime architecture

The canonical read-only flow remains:

```text
diagnose -> plan -> dry-run -> approval -> run -> run history -> audit
```

Key properties:

- authenticated sensitive endpoints;
- structural action allowlist validated at startup;
- human approval before a read-only run;
- fixed internal runner functions with no request-provided command or argv;
- bounded output, timeout, sanitized environment and redaction;
- persistent JSONL audit, approval and run-history stores;
- no free shell, SSH, `docker exec`, free PromQL or automatic deploy.

The official runner is `app/agent_router/services/action_runner.py`. Legacy
executors under `app/adapters/` are compatibility code and are not part of the
official execution path.

## AgentReview v0.20.0

The offline deterministic pipeline is:

```text
aiops-intake.json + redaction-report.json
-> semantic-chunk-plan.json
-> pr-brief.json + chunk-payload-manifest.json + chunk-payloads/
-> chunk-results.json
-> final-review.json + final-review.md
-> review-quality-gate.json
-> review-telemetry.json
-> optional false-positive-signatures.json
-> optional suggested-contract-updates.yaml
```

### Canonical authority

`review-quality-gate.json` is the canonical post-synthesis decision authority.
`final-review.json` is a synthesis artifact and must not be used as a fallback
authority when the gate is missing, malformed, incompatible, unknown or
contradictory.

Consumers must validate the gate schema, source, version, enumerations and
allowed field combinations before publication. Invalid gates produce a
deterministic fail-closed, non-conclusive result with manual review required.

### Deterministic context contract

- `pr-brief.json` is the sanitized deterministic PR summary;
- `chunk-payload-manifest.json` records the bounded payload set and hashes;
- each `chunk-payloads/<chunk_id>.json` contains isolated context for one
  semantic chunk and a complete structured response contract;
- truncation and coverage impact are explicit;
- response-compatible `chunk_id` validation is shared and fail-closed;
- path-bearing global validation evidence reaches every chunk while retaining
  sanitized provenance;
- non-global path-scoped evidence remains restricted to matching chunks.

### Telemetry and learning

`review-telemetry.json` observes the already-produced final review and gate. It
does not alter verdicts. False-positive signatures are deterministic, and
`suggested-contract-updates.yaml` is always human-reviewable, `manual_only` and
`applied: false`.

## Target-repository consumption

AgentEscala remains responsible for target-repository orchestration, optional
approved Agent Router calls and GitHub publication. It must consume this
toolrepo from an immutable canonical lowercase 40-character commit SHA.

The wrapper must not:

- resolve an operational branch, tag, short SHA or floating default branch;
- reimplement parsing, synthesis, quality-gate or telemetry logic;
- call `/v1/chat/ingest`;
- call a provider directly;
- treat `final-review.json` as substitute authority;
- apply contract suggestions automatically.

## Safety boundaries

The AgentReview CLIs require:

```text
AIOPS_ENVIRONMENT=dev
AIOPS_NODE_ROLE=toolrepo
AIOPS_REPO_MODE=agent_review_tooling
AIOPS_PRODUCTION_RUNTIME=false
```

They fail closed in production/runtime mode and write outputs outside the
target repository. Published artifacts must be allowlisted, sanitized and
scanned for secrets and local absolute paths.

## Validation baseline

The release baseline passed:

- full offline Python test suite;
- focused AgentReview unit, CLI and E2E contracts;
- deterministic byte-output checks;
- target/source fixture immutability checks;
- production-boundary fail-closed checks;
- repository CI validation.

Canonical local commands:

```bash
python3 -m pytest tests -q
bash scripts/ci_validate.sh
git diff --check
```

Runtime validation remains a separate CT102-only, explicitly authorized
operation. Offline documentation or AgentReview work does not authorize it.

## Explicitly absent or out of scope

- free shell or request-provided commands;
- SSH or `docker exec` in the official runner;
- automatic deploy, remediation, approval or merge;
- AgentReview on CT102;
- direct provider calls from the AIOps AgentReview CLIs;
- real second-opinion implementation;
- automatic contract suggestion application;
- using telemetry score as a merge decision.

## Current follow-up direction

The `v0.20.0` release track is complete. Future work should be scoped in
separate issues and releases. Candidate areas are target-repository wrapper
adoption, validation-evidence enrichment and optional second-opinion design.
None of those areas changes the `v0.20.0` contract retroactively.

## Canonical references

- [Architecture](ARCHITECTURE.md)
- [Project manual](AIOPS_PROJECT_MANUAL.md)
- [AgentReview engine](AGENT_REVIEW_ENGINE.md)
- [AgentReview E2E pipeline](AGENT_REVIEW_E2E_PIPELINE.md)
- [AgentReview quality gate](AGENT_REVIEW_QUALITY_GATE.md)
- [AgentEscala target-repository contract](AGENTESCALA_TARGET_REPO_CONTRACT.md)
- [Release v0.20.0](RELEASE_V0_20_0.md)
- [Environment boundaries](ENVIRONMENT_BOUNDARIES.md)
- [Testing](TESTING.md)
