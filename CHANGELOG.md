# Changelog

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
