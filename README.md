# AIOps Orchestrator

Serviço de orquestração de automações de infraestrutura via LLM. Executa comandos
em hosts (local, SSH, Docker) a partir de instruções em linguagem natural
validadas contra policies YAML.

Integra-se com o [`agent-router-api`](https://github.com/mglpsw/agent-router-api)
através da rota `@aiops` do router.

Extraído de `homelab/aiops` em 2026-04-18.

---

## Funcionalidades

- **API REST FastAPI** na porta 8000
- **Adaptadores LLM**: Ollama, Claude, OpenAI/Codex
- **Adaptadores executor**: local shell, SSH, Docker
- **Policy engine**: YAML-based rules (allow/deny por pattern)
- **Provider registry**: abstração para múltiplos LLMs
- **Autenticação por token** (`AIOPS_API_TOKEN`)
- **Métricas Prometheus**: `/metrics`
- **SQLite persistence** para savings/histórico

---

## Quick Start

```bash
cp .env.example .env
# Edit .env (AIOPS_API_TOKEN, OLLAMA_HOST, API keys)

cd deploy
docker compose up -d aiops-orchestrator
curl -H "Authorization: Bearer $AIOPS_API_TOKEN" http://localhost:8000/health
```

### Integração com agent-router-api

O orchestrator compartilha a network Docker `aiops-net`. Deploy o
`agent-router-api` primeiro (ele cria a network), depois este serviço.

```bash
# No agent-router-api repo:
cd deploy && docker compose up -d
# No aiops-orchestrator repo:
cd deploy && docker compose up -d
```

O router então encaminha `@aiops` para `http://aiops-orchestrator:8000`.

---

## Endpoints

| Path | Descrição |
|---|---|
| `/health` | Healthcheck |
| `/metrics` | Métricas Prometheus |
| `/v1/task` | Executa tarefa (autenticado) |
| `/v1/task/{id}` | Consulta status |
| `/v1/providers` | Lista provedores disponíveis |

Ver `docs/OPERATIONS.md` para detalhes.

### AIOps Diagnose Endpoint v1

- `POST /v1/aiops/diagnose`
- Request: `AIOpsDiagnoseRequest`
- Response: `AIOpsDiagnoseResponse`
- Diagnostic-only
- `dry_run` obrigatório e sempre `true`
- Sem execução, remediação ou `command`
- Checks suportados: `readiness`, `backend_up`, `error_rate`, `latency_p95`, `blocked_tasks`, `model_selection`, `ollama_models_count`

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
