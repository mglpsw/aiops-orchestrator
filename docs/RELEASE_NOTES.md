# Release Notes

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
