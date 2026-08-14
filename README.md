# AIOps Orchestrator

Serviço de orquestração AIOps com duas superfícies deliberadamente separadas:

- **runtime AIOps** para diagnóstico, planejamento, dry-run, aprovação, execução
  read-only allowlisted e histórico auditável;
- **AgentReview**, engine offline de revisão para intake sanitizado, semantic
  chunking, PR brief, payloads limitados, parsing, síntese, quality gate e
  telemetria determinísticos.

O AgentReview existe hoje em duas linhas: a **v1**, publicada e em manutenção/freeze,
e a **v2**, sucessora em desenvolvimento, que inclui o target pack de distribuição.
As duas coexistem deliberadamente — ver [Componentes e estado](#componentes-e-estado).

O runtime roda no CT102 e o AgentReview no CT104. Essa é a topologia de deployment
atual, não uma propriedade dos componentes: ver
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) para a separação entre arquitetura
lógica e topologia.

Os adaptadores legados de shell/SSH/Docker continuam no repositório por
compatibilidade histórica, mas não são o caminho oficial do runner. O
AgentReview não executa no CT102 e não chama providers diretamente.

Integra-se com o [`agent-router-api`](https://github.com/mglpsw/agent-router-api)
através da rota `@aiops` do router.

Repo canônico da CT 102: `/opt/aiops-orchestrator`.
As surfaces legadas continuam compatíveis, mas estão marcadas como deprecated e devem migrar
para as APIs canônicas `/v1/aiops/*`.

## Componentes e estado

```text
AIOps ecosystem
│
├── AIOps Runtime          diagnose → plan → dry-run → approval → read-only run → audit
│
├── AgentReview
│   ├── v1                 released / maintenance / freeze
│   └── v2                 successor em desenvolvimento
│       └── Target Pack    instalação/distribuição multi-repo
│
├── CAEM                   contratos, evidência, reprodutibilidade
│
└── Agent Router           transporte de inferência e seleção de provider/modelo
```

| Componente | Estado | Observação |
|---|---|---|
| AIOps Runtime | **released** | última implantação registrada `0.20.0` (não é observação viva do CT102); versionado à parte da tag do toolrepo |
| AgentReview v1 | **released / maintenance / freeze** | baseline `v0.22.0`; só correções críticas/segurança/regressão |
| AgentReview v2 | **em desenvolvimento** | não é GA, não é default, não é required check |
| Target Pack v2 | **em desenvolvimento** | `init`/`doctor` implementados; demais subcomandos deferidos |
| CAEM | **F0 pinado** | `development_freeze`, `published=false`; a norma pertence a `mglpsw/caem` |
| Agent Router | **integrado** | transporte de inferência; nunca autoridade sobre verdict |

Um componente "em desenvolvimento" não é utilizável em produção nem substitui o v1.
O v2 ser o sucessor **não** significa que o v1 será removido agora.

## Release e estado de `master`

- Última release publicada: **`v0.22.0`**, final e imutável, em 12 de agosto de 2026,
  no commit `2ce1f45768b8779cb48ef8a302d4ed796349f0e5`.
- `master` está **à frente** da `v0.22.0` com trabalho ainda **não publicado em
  release**: seis slices do AgentReview v2 (incluindo a primeira do target pack) e
  uma correção crítica do AgentReview v1 (`#225`). Uma dessas slices (`8b20ae3`)
  também **toca** superfície CAEM/shared — move/reutiliza primitives de JSON
  estrito/digest entre `app/caem_consumer/f0.py` e `app/common/strict_json.py`;
  isso é uma superfície tocada, não uma mudança de comportamento comprovada
  (testes de equivalência visam preservá-lo).
- Consumidores devem pinar um SHA completo e imutável, nunca branch ou tag móvel.
  Um consumidor pinado na `v0.22.0` **não** possui a correção `#225`.

Histórico: [notas de release](docs/RELEASE_NOTES.md) e os snapshots
[`v0.22.0`](docs/RELEASE_V0_22_0.md), [`v0.21.0`](docs/RELEASE_V0_21_0.md),
[`v0.20.0`](docs/RELEASE_V0_20_0.md), [`v0.19.0`](docs/RELEASE_V0_19_0.md).
Roadmap canônico: [issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46).

## Por onde começar

| Quero… | Leia |
|---|---|
| entender o estado factual atual | [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) |
| entender a arquitetura | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| operar o runtime AIOps | [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| usar o AgentReview v1 | [`docs/AGENT_REVIEW_E2E_PIPELINE.md`](docs/AGENT_REVIEW_E2E_PIPELINE.md) |
| acompanhar o AgentReview v2 | [`docs/AGENT_REVIEW_V2_CONTRACTS.md`](docs/AGENT_REVIEW_V2_CONTRACTS.md) |
| instalar o target pack v2 | [`docs/AGENT_REVIEW_V2_TARGET_PACK.md`](docs/AGENT_REVIEW_V2_TARGET_PACK.md) |
| integrar um repositório alvo | [`docs/AGENTESCALA_TARGET_REPO_CONTRACT.md`](docs/AGENTESCALA_TARGET_REPO_CONTRACT.md) |

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

### AgentReview v1 — pipeline offline

Linha publicada e em manutenção/freeze. O pipeline determinístico roda somente em
CT104/dev/toolrepo:

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

### AgentReview v2 — sucessor em desenvolvimento

Linha sucessora, **não GA e não default**. Recebe toda a engenharia nova: contratos
com binding verificado, extração e redação de conteúdo real, proveniência
autoritativa de checks, readiness determinístico e o **target pack**, cujo papel
final é instalar o engine em repositórios alvo sem forkar o engine — a slice
atual (`init`/`doctor`) faz seed apenas de profile/integration metadata, não
instala o engine ainda; ver [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Adoção atual é `shadow`/opt-in e pinada separadamente do v1. Nenhum consumidor tem o
v2 como autoridade de merge. Contratos: [`docs/AGENT_REVIEW_V2_CONTRACTS.md`](docs/AGENT_REVIEW_V2_CONTRACTS.md).
Target pack: [`docs/AGENT_REVIEW_V2_TARGET_PACK.md`](docs/AGENT_REVIEW_V2_TARGET_PACK.md).

### Project Status

- Veja [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) para o checkpoint factual atual
- Roadmap canônico: [issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46)

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

Canônicos:

- [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) — estado factual corrente
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitetura lógica e topologia

AgentReview v1 (contratos estáveis):

- [`docs/AGENT_REVIEW_E2E_PIPELINE.md`](docs/AGENT_REVIEW_E2E_PIPELINE.md)
- [`docs/AGENT_REVIEW_QUALITY_GATE.md`](docs/AGENT_REVIEW_QUALITY_GATE.md)
- [`docs/AGENTESCALA_TARGET_REPO_CONTRACT.md`](docs/AGENTESCALA_TARGET_REPO_CONTRACT.md)

AgentReview v2 (em desenvolvimento):

- [`docs/AGENT_REVIEW_V2_CONTRACTS.md`](docs/AGENT_REVIEW_V2_CONTRACTS.md)
- [`docs/AGENT_REVIEW_V2_TARGET_PACK.md`](docs/AGENT_REVIEW_V2_TARGET_PACK.md)
- [`docs/AGENT_REVIEW_V2_TRUSTED_CHECKS.md`](docs/AGENT_REVIEW_V2_TRUSTED_CHECKS.md)

Operação e referência:

- [`docs/AIOPS_PROJECT_MANUAL.md`](docs/AIOPS_PROJECT_MANUAL.md)
- [`docs/README_AI_REVIEWER_DOCS.md`](docs/README_AI_REVIEWER_DOCS.md)
- [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md)
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
