# AIOps Orchestrator

Serviço de orquestração AIOps com duas superfícies deliberadamente separadas:

- runtime produtivo no CT102 para diagnóstico, planejamento, dry-run, aprovação,
  execução read-only allowlisted e histórico auditável;
- AgentReview offline no CT104 para intake sanitizado, semantic chunking, PR brief,
  payloads limitados, parsing, síntese, quality gate e telemetria determinísticos.

Os adaptadores legados de shell/SSH/Docker continuam no repositório por
compatibilidade histórica, mas não são o caminho oficial do runner. O
AgentReview não executa no CT102 e não chama providers diretamente.

Integra-se com o [`agent-router-api`](https://github.com/mglpsw/agent-router-api)
através da rota `@aiops` do router.

Repo canônico da CT 102: `/opt/aiops-orchestrator`.
As surfaces legadas continuam compatíveis, mas estão marcadas como deprecated e devem migrar
para as APIs canônicas `/v1/aiops/*`.

## Release v0.20.0

Release final assinada publicada em 19 de julho de 2026 no commit
`13695c73d1da9f16eba5c20e6478e7d51aefbb45`.

- `review-quality-gate.json` como autoridade canônica pós-síntese;
- E2E offline cobrindo intake, planejamento semântico, PR brief, payloads por
  chunk, parsing, síntese, quality gate e telemetria;
- telemetria determinística, assinaturas de falso positivo e sugestões de
  contrato sempre `manual_only`;
- contratos sanitizados e limitados de `pr-brief.json`,
  `chunk-payload-manifest.json` e `chunk-payloads/<chunk_id>.json`;
- consumo pelo target repo fixado a um SHA completo e imutável;
- runtime CT102 validado em `0.20.0`, sem migração de banco ou mudança de API.

Veja [Release v0.20.0](docs/RELEASE_V0_20_0.md) e as
[notas de release](docs/RELEASE_NOTES.md).

---

## Funcionalidades

- **API REST FastAPI** na porta 8000
- **Adaptadores LLM**: Ollama, Claude, OpenAI/Codex
- **Adaptadores executor legados**: local shell, SSH, Docker (quarentenados; não são o caminho oficial)
- **Policy engine**: YAML-based rules (allow/deny por pattern)
- **Provider registry**: abstração para múltiplos LLMs
- **Autenticação por token** (`AGENT_ROUTER_API_TOKEN` ou `AIOPS_API_TOKEN`)
- **Métricas Prometheus**: `/metrics`
- **SQLite persistence** para savings/histórico
- **AgentReview offline** com artifacts determinísticos e quality gate fail-closed

---

## Quick Start

```bash
cp .env.example .env
# Edit .env (AGENT_ROUTER_API_TOKEN, OLLAMA_HOST, API keys)

cd /opt/aiops-orchestrator
docker compose -f deploy/docker-compose.yml up -d aiops-orchestrator
curl -H "Authorization: Bearer $AGENT_ROUTER_API_TOKEN" http://localhost:8000/health
```

O compose principal de produção não monta `/var/run/docker.sock`. Para manutenção
explícita, use o override `deploy/docker-compose.maintenance.yml`.

### Integração com agent-router-api

O orchestrator compartilha a network Docker `aiops-net`. Deploy o
`agent-router-api` primeiro (ele cria a network), depois este serviço.

Siga o runbook do `agent-router-api` para criar a network compartilhada. Depois,
no clone produtivo deste repositório, use apenas o fluxo de deploy aprovado para
o ambiente. Não use o quick start como autorização de deploy.

O router então encaminha `@aiops` para `http://aiops-orchestrator:8000`.

---

## Endpoints

| Path | Descrição |
|---|---|
| `/health` | Healthcheck |
| `/healthz` | Healthcheck alias |
| `/ready` | Readiness check |
| `/readyz` | Readiness alias |
| `/metrics` | Métricas Prometheus |
| `/v1/chat` | Ingestão de chat (autenticado) |
| `/v1/chat/ingest` | Alias de ingestão de chat (autenticado) |
| `/v1/tasks` | Lista tarefas (autenticado) |
| `/v1/tasks/{id}` | Consulta status |
| `/v1/providers` | Lista provedores disponíveis |

Ver `docs/OPERATIONS.md` para detalhes.

Os endpoints de chat também reconhecem intents AIOps em pt-BR, incluindo diagnóstico, status,
runs e approvals, com resposta curta, segura e sem execução de actions. Veja
[`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) para o fluxo OpenWebUI.

### Autenticação

- Envie `Authorization: Bearer <token>` ou `X-Agent-Router-Token: <token>`.
- Configure o token com `AGENT_ROUTER_API_TOKEN` ou, para compatibilidade, `AIOPS_API_TOKEN`.
- Rotas protegidas: `POST /v1/chat`, `POST /v1/chat/ingest`, `GET /v1/tasks`, `GET /v1/tasks/{id}`, `GET /v1/approvals`, `POST /v1/approvals/{task_id}`, `GET /v1/providers/status`, `POST /v1/aiops/diagnose` e qualquer rota sensível de execução/planejamento existente.

### AIOps Diagnose Endpoint v1

- `POST /v1/aiops/diagnose`
- Request: `AIOpsDiagnoseRequest`
- Response: `AIOpsDiagnoseResponse`
- Diagnostic-only
- `dry_run` obrigatório e sempre `true`
- Sem execução, remediação ou `command`
- `health_score` de `0` a `100`, calculado de forma determinística a partir dos findings/checks
- Checks suportados: `readiness`, `backend_up`, `error_rate`, `latency_p95`, `blocked_tasks`, `model_selection`, `ollama_models_count`
- Campo `action_plan` (opcional): quando há findings com problema, o response inclui um `ActionPlanResponse` com `action_ids` sugeridos do catálogo allowlisted (`dry_run: true`, sem `command`). Retorna `null` quando status é `ok` ou o catálogo não está disponível.

### Action Catalog e Action Planner (v1)

- `GET /v1/aiops/actions/catalog` — lista o catálogo de ações allowlisted (autenticado, sem expor comandos)
- `POST /v1/aiops/actions/plan` — gera plano determinístico a partir de `action_ids` explícitos (autenticado, `dry_run` sempre `true`)
- `POST /v1/aiops/actions/dry-run` — simula um plano allowlisted sem executar nada (autenticado, `dry_run` sempre `true`)
- `POST /v1/aiops/actions/approvals` — cria uma aprovação persistente para `plan_id` ou `dry_run_id`
- `GET /v1/aiops/actions/approvals/{approval_id}` — consulta uma aprovação persistente
- `POST /v1/aiops/actions/approvals/{approval_id}/approve` — aprova uma solicitação pendente
- `POST /v1/aiops/actions/approvals/{approval_id}/reject` — rejeita uma solicitação pendente
- `POST /v1/aiops/actions/run` — executa apenas funções internas read-only allowlisted após aprovação válida
- `GET /v1/aiops/audit/recent` — retorna os eventos auditados mais recentes
- Somente `action_ids` presentes em `config/actions.yaml` são aceitos
- Nenhum comando livre, shell, SSH, remediação automática ou bridge futura é aceita nesta fase
- `action_ids` desconhecidos vão para `blocked_steps` (fail-closed)
- O catálogo é validado no **startup** da aplicação; falha degrada `/ready` para `not_ready` antes da primeira requisição
- Ver `docs/ACTIONS.md` para schema, regras, validação no startup e processo de adição futura

### Audit log

- O audit log v1 registra metadados estruturados de `plan` e `dry-run` em JSONL
- Caminho padrão: `var/audit/aiops_audit.jsonl`
- Variáveis:
- `AIOPS_AUDIT_LOG_PATH`
- `AIOPS_AUDIT_LOG_REQUIRED`
- `AIOPS_AUDIT_LOG_MAX_BYTES`
- `AIOPS_AUDIT_LOG_BACKUP_COUNT`
- `AIOPS_AUDIT_LOG_ROTATION_ENABLED`
- Nenhum `command`, segredo ou cabeçalho sensível é persistido
- `GET /v1/aiops/audit/recent` permite inspeção autenticada dos eventos mais recentes

### Approval model

- Aprovações são persistidas de forma estruturada e não executam ações
- Caminho padrão: `var/approvals/aiops_approvals.jsonl`
- Variável: `AIOPS_APPROVAL_STORE_PATH`
- `ttl_seconds` padrão: `900`
- TTL máximo seguro: `3600`
- Estados: `pending`, `approved`, `rejected`, `expired`
- Aprovações e decisões são auditadas

### Read-only run v1

- Endpoint: `POST /v1/aiops/actions/run`
- Variáveis:
  - `AIOPS_RUN_STORE_PATH`
  - `AIOPS_RUN_TIMEOUT_SECONDS`
  - `AIOPS_RUN_OUTPUT_MAX_BYTES`
  - `AIOPS_RUN_STORE_MAX_RECORDS`
  - `AIOPS_ACTION_REPO_ROOT`
- Histórico:
  - `GET /v1/aiops/runs/recent`
  - `GET /v1/aiops/runs/{run_id}`
- Executa apenas funções internas fixas read-only e allowlisted
- Nesta fase, o subconjunto executável inclui health/ready de `8000` e `8001`,
  `git_status`, `git_diff_stat`, `docker_compose_config`, `docker_compose_bluegreen_config`,
  `systemctl_status_aiops`, `journalctl_aiops_recent` e `prometheus_query_allowlisted`
- Também suporta inspeção local read-only via `git_status`, `git_diff_stat`, `docker_compose_config`,
  `docker_compose_bluegreen_config` e `systemctl_status_aiops`
- As inspeções locais destacadas nesta sessão são `git_status` e `docker_compose_config`
- `docker_compose_config` e `docker_compose_bluegreen_config` usam validação `docker compose ... config --quiet`
- `git_diff_stat` usa somente `git diff --stat`
- `systemctl_status_aiops` consulta apenas o estado read-only da unit `aiops-orchestrator.service`
- `systemctl_status_aiops` não reinicia, não recarrega e não altera o serviço
- `journalctl_aiops_recent` consulta apenas logs recentes e limitados do serviço,
  com janela fixa de 15 minutos, limite fixo de 100 linhas, `--no-pager` e sem follow
- `journalctl_aiops_recent` pode conter segredos em logs, então a redaction é forte e obrigatória
- `prometheus_query_allowlisted` consulta um bundle fixo de métricas do Prometheus sem aceitar
  PromQL livre; a URL base é `AIOPS_PROMETHEUS_BASE_URL` (default `http://127.0.0.1:9090`)
- O bundle Prometheus v1 usa apenas queries internas allowlisted e não aceita `query`, `args`,
  `target` ou `URL` vindos do request
- O repositório alvo é fixo/allowlisted via `AIOPS_ACTION_REPO_ROOT`
- Não aceita `command` ou `argv` no request e não expõe `command` ou `argv` na resposta
- Requer approval válido e audit log ativo
- O executor oficial atual é apenas `app/agent_router/services/action_runner.py`
- `GitHub Bridge`, `Claude Bridge` e `Codex Bridge` continuam fora desta fase

### GitHub Agent Review on-demand

- Comentários em PR com `/agent review` ou `/agent review llm` acionam o workflow `agent-review`
- `/agent ask <pergunta>` publica uma resposta separada e contextual, sem sobrescrever o review principal
- Veja [`docs/GITHUB_AGENT.md`](docs/GITHUB_AGENT.md) para o contrato, autorização e modo LLM opcional

### AgentReview offline v0.20.0

O pipeline determinístico roda somente em CT104/dev/toolrepo:

```text
intake/redaction
-> semantic chunk plan
-> PR brief + bounded chunk payloads
-> structured chunk parsing
-> final synthesis
-> review quality gate
-> telemetry
-> optional false-positive signatures and manual-only suggestions
```

`review-quality-gate.json` é a autoridade de decisão pós-síntese. O target repo
deve validar schema, source, versão e combinações permitidas e falhar fechado
quando o gate estiver ausente, inválido ou contraditório. Consulte o
[contrato E2E](docs/AGENT_REVIEW_E2E_PIPELINE.md) e o
[contrato do quality gate](docs/AGENT_REVIEW_QUALITY_GATE.md).

### Project Status

- Veja [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) para o checkpoint canônico atual e o roadmap imediato

---

## Tests

```bash
python3 -m pytest tests -q
bash scripts/ci_validate.sh
```

GitHub Actions CI validates the action catalog, scripts, compose configs, and tests on
`push` and `pull_request` to `main` and `master`. It does not deploy anything; deploy remains
manual and approved.

---

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md)
- [`docs/AIOPS_PROJECT_MANUAL.md`](docs/AIOPS_PROJECT_MANUAL.md)
- [`docs/README_AI_REVIEWER_DOCS.md`](docs/README_AI_REVIEWER_DOCS.md)
- [`docs/AGENT_REVIEW_E2E_PIPELINE.md`](docs/AGENT_REVIEW_E2E_PIPELINE.md)
- [`docs/AGENT_REVIEW_QUALITY_GATE.md`](docs/AGENT_REVIEW_QUALITY_GATE.md)
- [`docs/RELEASE_V0_20_0.md`](docs/RELEASE_V0_20_0.md)
- [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- [`docs/SECURITY.md`](docs/SECURITY.md)
- [`docs/LEGACY_ADAPTERS.md`](docs/LEGACY_ADAPTERS.md)
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
- [`docs/ROLLBACK.md`](docs/ROLLBACK.md)
- [`docs/aiops-bluegreen-operations.md`](docs/aiops-bluegreen-operations.md)
- [`docs/bluegreen-deployment.md`](docs/bluegreen-deployment.md)

---

## KS-SM Labs

Este repositório faz parte do ecossistema KS-SM Labs
([github.com/homelab-mglpsw](https://github.com/homelab-mglpsw)), que
centraliza projetos de homelab, AIOps, observabilidade e agentes inteligentes.

O `aiops-orchestrator` concentra a camada de orquestração AIOps: diagnóstico assistido,
automações operacionais e integração com métricas reais do homelab. Por poder evoluir para
executar ações operacionais, é tratado com cautela especial dentro da organização.

Consulte [`docs/repo-metadata.md`](docs/repo-metadata.md) para metadados, permissões e
diretrizes de branch protection recomendadas para este repositório.
