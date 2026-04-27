# Changelog

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
