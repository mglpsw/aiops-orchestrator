# Changelog

## Unreleased

### Added

- `config/ri/ri-b0a-2-reuse-manifest.json` + generated view
  `docs/generated/RI_B0A_2_REUSE_REFERENCE.md`: maps all 10 existing
  AgentReview v2 contracts plus the ProjectOps track boundary into one of
  four states (`reuse`, `reference`, `future_adapter`, `not_applicable`)
  for RI-B0, with a fail-closed loader (`app/ri_b0a/reuse_manifest.py`)
  and a `--check` CI gate. No AgentReview or CAEM schema is copied or
  redefined (refs #119, slice #119.2)

- `docs/RI_A2_THREAT_MODEL.md`: threat model, data policy, and authority
  boundaries for CAEM proof execution — 15 threat surfaces, a trust-boundary
  diagram, an authority/data matrix across 10 components, stop conditions
  for RI-B0/RI-B1, and an adversarial test corpus. Documentation only; no
  code, DLP detector, executor, auth, database, or deploy change (refs #120,
  parent #126)

- CAEM 3.0 F0 consumer pin (`config/caem/caem-3.0-f0.pin.json`) and a strict,
  offline, fail-closed loader (`app/caem_consumer/f0.py`,
  `scripts/verify-caem-f0-pin.py`) that verifies the pinned identity against a
  local artifact copy byte-for-byte; the exact CAEM 3.0 F0 carrier
  (`28ca73f3…`) is the only identity this consumer accepts — `main`, any
  branch/tag, and the post-F0 repair carrier (`dee7018e…`) are all rejected
  (refs mglpsw/aiops-orchestrator#119, slice #119.1)
- CAEM 2.1.0 material previously vendored under `.caem/` (`policy.json`,
  `repository-profile.json`, `repository-registry.json`, `schemas/*.json`) is
  quarantined, read-only, `authority_effect=none`
  (`.caem/quarantine/caem-2.1/`); `AGENTS.md`, `CLAUDE.md`,
  `docs/engineering/{CAEM_CORE,PROJECT_OVERLAY,CURRENT_CHECKPOINT}.md` now
  reference the single consumer pin instead of declaring CAEM 2.1.0/2.2.0
  digests that no longer correspond to anything in this checkout

- AgentReview v2: explicit v1/v2 contract-version selection
  (`app/agent_review/versioning.py`), rejecting unknown or mixed versions
  fail-closed
- AgentReview v2: verified payload-response binding wired into a new
  consumer and parser (`app/agent_review/consumer_v2.py`,
  `app/agent_review/parser_v2.py`); no v2 finding is reachable before run
  identity, HEAD, chunk, payload hash, response hash, file scope, and
  coverage-without-promotion are all proven (refs #83)
- `docs/AGENT_REVIEW_V2_BINDING.md` documents the version-selection
  contract, binding sequence, and reason-code precedence
- AgentReview v2: strict, fail-closed `TargetProfileV2` loader
  (`app/agent_review/profile_loader_v2.py`) with reproducible
  profile/policy hashing; no silent degradation to a placeholder profile
  (refs #85)
- AgentReview v2: explicit, non-destructive v1 -> v2 target-profile
  migrator (`app/agent_review/profile_migration_v1_v2.py` +
  `scripts/migrate-agent-review-profile-v1-v2.py`); never fabricates
  required checks, must-review rules, or contract hashes, and never runs
  automatically (refs #85)
- AgentReview v2: minimal, hash-pinned offline toolrepo lock
  (`requirements-agent-review.lock`,
  `scripts/install-agent-review-toolrepo.sh`); installs only what
  `app/agent_review` imports (`pydantic`, `PyYAML`, and pydantic's own
  dependencies), verified installable in a clean venv with
  `pip --require-hashes` (refs #85)
- `docs/AGENT_REVIEW_V2_TARGET_PROFILE.md` and
  `docs/AGENT_REVIEW_V2_INSTALLATION.md` document the loader, migrator, and
  installation contract
- AgentReview v2: typed, deny-unknown fragment manifest
  (`app/agent_review/manifest_v2.py`) with a structural losslessness
  invariant -- every must-review fragment is referenced by exactly one
  chunk or explicitly accounted for by a degradation cause; published as
  `schemas/agent-review/v2/agent-review.manifest.v2.schema.json` (refs #84)
- AgentReview v2: lossless line-range multi-chunk planner
  (`app/agent_review/planner_v2.py`) with an exact (not merely heuristic)
  bin-packing decision procedure for required content, and honest
  three-way reason codes (`budget_exhausted` / `packing_search_exhausted`
  / `planner_limit_exceeded`) distinguishing proven infeasibility from a
  bounded search's inconclusive result (refs #84)
- AgentReview v2: git unified-diff acquisition and parsing
  (`app/agent_review/diff_acquisition_v2.py`) -- renames, binaries
  (including the real `GIT binary patch` format), submodules, missing
  trailing newlines, and truncated hunks all recognized structurally,
  verified against real `git diff --binary` output (refs #84)
- AgentReview v2: payload builder (`app/agent_review/payload_builder_v2.py`)
  turning a planned manifest's chunks into actual `ChunkPayloadV2`
  objects, with N-chunk propagation through the existing #83
  consumer/parser proven end-to-end (refs #84)
- `docs/AGENT_REVIEW_V2_CHUNKING.md` documents the manifest, planner, diff
  acquisition, and payload builder, including the one deliberately
  deferred piece (symbol/AST-aware grouping, explicitly optional in the
  issue) and the documented file-vs-line-range coverage granularity gap

### Notes

- `app/agent_review/contracts_v2.py` (PRs #81/#82) is unmodified; this
  delivery only wires its existing authority into new v2-only call paths
- the v1 pipeline (`v0.20.0`) is unmodified and remains the only active
  operational path; v2 is not yet wired into any CLI or workflow

## v0.20.0 - 2026-07-19 - AgentReview Quality Gate

### Added

- deterministic post-synthesis quality gate with
  `review-quality-gate.json` as the canonical decision authority
- deterministic PR brief, bounded per-chunk payloads and payload manifest
- review telemetry, false-positive signatures and human-reviewable contract
  suggestions that remain `manual_only`
- offline E2E coverage from intake/redaction through telemetry

### Changed

- AgentEscala consumption contract now requires an immutable lowercase full
  commit SHA and fail-closed gate validation
- chunk payload contracts preserve validation risks, synthesizer facts,
  provenance and strict response-compatible chunk identity
- runtime-reported default version advanced from `0.19.0` to `0.20.0`

### Security

- no AgentReview execution on CT102
- no direct provider, `/v1/chat/ingest`, deploy, SSH, Docker or GitHub write
  call from the offline AIOps CLIs
- no automatic contract update, remediation, approval or merge
- sanitized artifacts reject secrets and local absolute-path leakage

### Release

- signed RC and final tags target
  `13695c73d1da9f16eba5c20e6478e7d51aefbb45`
- final GitHub release is non-draft, non-prerelease and signature-verified
- CT102 reported `0.20.0` with health, readiness, metrics, database, providers
  and action catalog ready
- rollback remains `v0.19.0`

## v0.19.0 - 2026-06-02 - AgentReview E2E and CT102 transition

### Added

- offline AgentReview intake, redaction, semantic chunk planning, structured
  chunk parsing and deterministic final synthesis
- AgentEscala thin-wrapper E2E validation on CT104
- explicit CT104 toolrepo and CT102 runtime environment boundaries

### Changed

- CT102 runtime-reported version advanced to `0.19.0`
- production health, readiness, metrics, stores, providers and action catalog
  were validated with documented rollback evidence

## v0.18.0 - AIOps readonly/chat checkpoint

### Added

- chat/OpenWebUI com intents AIOps determinísticas em pt-BR para diagnose, runs, approvals e status
- GitHub Agent Review com `/agent review`, `/agent review llm` e `/agent ask`
- follow-up contextual separado para `/agent ask`
- resposta pública em pt-BR por padrão com fallback seguro via `GITHUB_STEP_SUMMARY`
- diagnóstico severity-aware com findings enriquecidos e baseline temporal simples

### Changed

- runner read-only mantido em allowlist estrutural e fail-closed
- documentação alinhada ao fluxo canônico da fase readonly/chat
- review e chat seguem sem execução de código do PR, SSH, shell livre, deploy automático ou ações não allowlisted

### Security

- sem shell livre
- sem `docker exec`
- sem PromQL livre
- sem persistir `command`, `argv`, tokens, headers ou payloads brutos sensíveis
- sem GitHub Bridge real, Local Agent Bridge genérico ou Claude/Codex Bridge

### Validation

- suíte completa e scripts de validação passam
- catálogo de actions validado no startup e no CI
- composes base e blue/green seguem válidos
- redaction e fallback do `/agent ask` permanecem cobertos por teste

### Known limitations

- o GitHub Agent Review ainda depende de permissão para comentar no PR
- quando o comentário não pode ser publicado, o fallback seguro vai para `GITHUB_STEP_SUMMARY`
- o runner continua estritamente read-only nesta fase

### Next: agent-router-api

O próximo foco recomendado é `agent-router-api`, com fronteiras explícitas entre chat,
diagnóstico e qualquer superfície futura de execução.
