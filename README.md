# AIOps Orchestrator

Serviço de orquestração de automações de infraestrutura via LLM. Executa comandos
em hosts (local, SSH, Docker) a partir de instruções em linguagem natural
validadas contra policies YAML.

Integra-se com o [`agent-router-api`](https://github.com/mglpsw/agent-router-api)
através da rota `@aiops` do router.

Repo canônico da CT 102: `/opt/aiops-orchestrator`.

---

## Funcionalidades

- **API REST FastAPI** na porta 8000
- **Adaptadores LLM**: Ollama, Claude, OpenAI/Codex
- **Adaptadores executor**: local shell, SSH, Docker
- **Policy engine**: YAML-based rules (allow/deny por pattern)
- **Provider registry**: abstração para múltiplos LLMs
- **Autenticação por token** (`AGENT_ROUTER_API_TOKEN` ou `AIOPS_API_TOKEN`)
- **Métricas Prometheus**: `/metrics`
- **SQLite persistence** para savings/histórico

---

## Quick Start

```bash
cp .env.example .env
# Edit .env (AGENT_ROUTER_API_TOKEN, OLLAMA_HOST, API keys)

cd /opt/aiops-orchestrator
docker compose -f deploy/docker-compose.yml up -d aiops-orchestrator
curl -H "Authorization: Bearer $AGENT_ROUTER_API_TOKEN" http://localhost:8000/health
```

### Integração com agent-router-api

O orchestrator compartilha a network Docker `aiops-net`. Deploy o
`agent-router-api` primeiro (ele cria a network), depois este serviço.

```bash
# No agent-router-api repo:
cd /opt/aiops-orchestrator && docker compose -f deploy/docker-compose.yml up -d
# No aiops-orchestrator repo:
cd deploy && docker compose up -d
```

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
- Checks suportados: `readiness`, `backend_up`, `error_rate`, `latency_p95`, `blocked_tasks`, `model_selection`, `ollama_models_count`

### Action Catalog e Action Planner (v1)

- `GET /v1/aiops/actions/catalog` — lista o catálogo de ações allowlisted (autenticado, sem expor comandos)
- `POST /v1/aiops/actions/plan` — gera plano determinístico a partir de `action_ids` explícitos (autenticado, `dry_run` sempre `true`)
- Somente `action_ids` presentes em `config/actions.yaml` são aceitos
- Nenhum comando livre, shell, SSH ou remediação automática
- `action_ids` desconhecidos vão para `blocked_steps` (fail-closed)
- Ver `docs/ACTIONS.md` para schema, regras e processo de adição futura

---

## Tests

```bash
pytest tests/ -v
```

---

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)
- [`docs/SECURITY.md`](docs/SECURITY.md)
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md)
- [`docs/ROLLBACK.md`](docs/ROLLBACK.md)
- [`docs/aiops-bluegreen-operations.md`](docs/aiops-bluegreen-operations.md)
- [`docs/bluegreen-deployment.md`](docs/bluegreen-deployment.md)
