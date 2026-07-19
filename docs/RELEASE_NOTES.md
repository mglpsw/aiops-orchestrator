# Release Notes

## v0.20.0 - AgentReview Quality Gate

### Final identity

- Final tag: `v0.20.0`
- RC tag: `v0.20.0-rc.1`
- Shared target SHA:
  `13695c73d1da9f16eba5c20e6478e7d51aefbb45`
- Signed with SSH/ED25519 and verified locally and by GitHub

### Highlights

- deterministic `review-quality-gate.json` as canonical post-synthesis
  decision authority
- offline E2E pipeline covering intake, semantic chunk planning, PR brief,
  bounded chunk payloads, parsing, synthesis, gate and telemetry
- deterministic false-positive signatures and human-reviewable
  `suggested-contract-updates.yaml`, always `manual_only`
- immutable full-SHA checkout contract for target-repository consumption
- strict payload schemas preserving validation risks, synthesizer facts,
  provenance and response-compatible chunk identity

### Compatibility

- no database migration
- no provider, route, action-catalog or runtime API behavior change
- existing runtime configuration remains compatible
- runtime-reported application version advanced from `0.19.0` to `0.20.0`

### Production validation

- CT102 runtime version `0.20.0`
- health, readiness and metrics passed
- database, providers and action catalog ready
- no critical runtime errors, restarts or OOM kill
- previous `0.19.0` image retained for rollback
- `aiops-orchestrator-next` unchanged

See [`RELEASE_V0_20_0.md`](RELEASE_V0_20_0.md) for the complete contract and
release evidence.

---

## v0.19.0 - AgentReview E2E and CT102 runtime transition

- finalized the first offline AgentReview E2E line
- validated the AgentEscala thin-wrapper contract on CT104
- completed the controlled CT102 runtime transition to `0.19.0`
- established `v0.19.0` as the rollback release for `v0.20.0`

---

## v0.18.0-hotfix.1 - Docker build project name alignment

### Fixed

- Docker Compose build now uses consistent project name `-p deploy` for both build and up commands
- **Problem:** Previous build generated `aiops-orchestrator-aiops-orchestrator` image but production container used `deploy-aiops-orchestrator`, causing stale code in runtime
- **Impact:** Session L1 legacy deprecation observability (headers, logging, metrics) now properly active in container
- **Verification:** `/health` ✓, `/ready` ✓, Deprecation headers ✓, Warning headers ✓, `aiops_legacy_endpoint_hits_total` metrics ✓

### Build Command (Corrected)

```bash
docker compose -p deploy -f deploy/docker-compose.yml build --no-cache aiops-orchestrator
docker compose -p deploy -f deploy/docker-compose.yml up -d --force-recreate --no-deps aiops-orchestrator
```

---

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

## Checkpoint da fase AIOps

Esta sequência consolidou a base canônica do AIOps Orchestrator em modo seguro,
read-only e auditável.

- runner read-only allowlisted
- approval gate persistente
- histórico de runs e auditoria
- redaction forte de segredos, tokens e headers
- Prometheus allowlisted sem PromQL livre
- diagnóstico inteligente com findings estruturados
- GitHub Agent Review on-demand
- chat/OpenWebUI com intents AIOps determinísticas

### Garantias preservadas

- sem shell livre
- sem SSH
- sem `docker exec`
- sem deploy automático
- sem actions novas
- sem runner novo
- sem execução de actions pelo chat
- sem exposição de secrets, headers ou payload bruto

### Próxima fase

O próximo foco recomendado é `agent-router-api`, com fronteiras explícitas entre chat,
diagnóstico e qualquer superfície futura de execução.
