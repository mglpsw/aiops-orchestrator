# AIOps Replacement Audit

## Conclusão

**Status:** `partially_replaced`

O AIOps novo já substitui o caminho canônico de diagnóstico, planejamento, dry-run, aprovação, run read-only, histórico e auditoria. No entanto, o repositório ainda mantém surfaces legadas ativas para chat, tarefas, approvals e provider health, além de adapters antigos e do `provider_registry` histórico. Isso significa que a substituição **ocorreu parcialmente**, mas **não está completa**.

## Mapa do AIOps novo

O caminho canônico atual fica em `app/agent_router/` e nas rotas novas expostas pelo FastAPI:

- `app/agent_router/main.py`
- `app/agent_router/schemas.py`
- `app/agent_router/services/aiops_diagnostic.py`
- `app/agent_router/services/health_score.py`
- `app/agent_router/services/action_mapper.py`
- `app/agent_router/services/action_planner.py` via integração atual
- `app/agent_router/services/action_dry_run.py`
- `app/agent_router/services/approval_store.py`
- `app/agent_router/services/audit_log.py`
- `app/agent_router/services/action_runner.py`
- `app/agent_router/services/run_store.py`

Surfaces canônicas atuais:

- `POST /v1/aiops/diagnose`
- `GET /v1/aiops/actions/catalog`
- `POST /v1/aiops/actions/plan`
- `POST /v1/aiops/actions/dry-run`
- `POST /v1/aiops/actions/approvals`
- `GET /v1/aiops/actions/approvals/{approval_id}`
- `POST /v1/aiops/actions/approvals/{approval_id}/approve`
- `POST /v1/aiops/actions/approvals/{approval_id}/reject`
- `POST /v1/aiops/actions/run`
- `GET /v1/aiops/runs/recent`
- `GET /v1/aiops/runs/{run_id}`
- `GET /v1/aiops/audit/recent`

Características do novo caminho:

- runner oficial único em `app/agent_router/services/action_runner.py`
- approval obrigatório antes do run read-only
- audit log JSONL estruturado
- run history JSONL consultável
- redaction forte de segredos e tokens
- timeout, cwd allowlisted e env sanitizado
- subprocess restrito apenas no helper controlado do runner
- ações read-only allowlisted e fixas

## Mapa do AIOps antigo/legado

O caminho legado ainda existe em várias partes do repositório:

- `app/main.py`
- `app/api/routes.py`
- `app/services/orchestrator.py`
- `app/services/provider_registry.py`
- `app/adapters/executor_local.py`
- `app/adapters/executor_ssh.py`
- `app/adapters/docker.py`
- `docs/aiops-audit.md`
- `docs/aiops-v1-plan.md`
- trechos legados em `docs/OPERATIONS.md`, `docs/TROUBLESHOOTING.md`, `docs/ROLLBACK.md`, `docs/INTEGRATIONS.md`
- testes de legado e quarentena em `tests/`

Surfaces antigas ainda ativas:

- `POST /v1/chat`
- `POST /v1/chat/ingest`
- `GET /v1/tasks`
- `GET /v1/tasks/{id}`
- `GET /v1/approvals`
- `POST /v1/approvals/{task_id}`
- `GET /v1/providers/status`

Essas rotas continuam sendo servidas pelo caminho legado de orchestrator/provider registry, não pelo runner novo.

## Arquivos legados ainda vivos

### Código

- `app/services/provider_registry.py`
- `app/services/orchestrator.py`
- `app/api/routes.py`
- `app/adapters/executor_local.py`
- `app/adapters/executor_ssh.py`
- `app/adapters/docker.py`

### Documentação histórica/legada

- `docs/aiops-audit.md`
- `docs/aiops-v1-plan.md`
- `docs/OPERATIONS.md`
- `docs/TROUBLESHOOTING.md`
- `docs/ROLLBACK.md`
- `docs/INTEGRATIONS.md`

### Testes que ainda exercitam isolamento/compatibilidade do legado

- `tests/test_legacy_adapter_quarantine.py`
- `tests/test_action_run.py`
- `tests/test_run_history.py`
- `tests/test_action_catalog.py`
- `tests/test_action_mapper.py`
- `tests/test_aiops_diagnostic.py`

## Imports legados ainda ativos

Evidência observada:

- `app/main.py` importa `get_registry` de `app.services.provider_registry`
- `app/api/routes.py` importa `get_registry`
- `app/services/orchestrator.py` importa `get_registry`
- `app/agent_router/signals.py` também usa `provider_registry`
- `app/services/provider_registry.py` importa:
  - `app.adapters.executor_local.LocalExecutorAdapter`
  - `app.adapters.executor_ssh.SSHExecutorAdapter`
  - `app.adapters.docker.DockerAdapter`

Conclusão sobre imports:

- os adapters legados ainda são importados por caminhos históricos
- o runner oficial novo **não** importa esses adapters
- o `/v1/aiops/actions/run` continua isolado do provider registry legado

## Endpoints/surfaces antigas ainda ativos

As seguintes superfícies ainda estão presentes e operacionais:

- chat/orquestração clássica:
  - `POST /v1/chat`
  - `POST /v1/chat/ingest`
- tarefas legadas:
  - `GET /v1/tasks`
  - `GET /v1/tasks/{id}`
- approvals legadas:
  - `GET /v1/approvals`
  - `POST /v1/approvals/{task_id}`
- providers legacy status:
  - `GET /v1/providers/status`

Essas superfícies dependem do caminho de provider/orchestrator antigo e não foram removidas nesta sessão.

## Riscos de remover agora

Remover o legado agora seria arriscado porque:

- `app/main.py` ainda inicializa o provider registry
- `app/api/routes.py` e `app/services/orchestrator.py` ainda dependem dele
- `GET /v1/providers/status` ainda é uma rota ativa
- os adapters legados ainda existem e podem ser usados por surfaces antigas
- os docs e testes ainda fazem referência explícita ao legado como compatibilidade histórica
- a remoção imediata pode quebrar consumidores antigos que ainda dependem das rotas clássicas

## Plano de substituição em fases

### Fase 1 — Congelar

- manter o runner novo como caminho oficial para AIOps v1
- manter o legado em compatibilidade histórica
- documentar explicitamente o legado como deprecated

### Fase 2 — Medir uso

- identificar consumidores das rotas clássicas
- registrar dependências de `GET /v1/providers/status`
- mapear se `app/services/orchestrator.py` ainda atende tráfego real

### Fase 3 — Migrar consumidores

- migrar clientes para o caminho canônico `app/agent_router/`
- trocar integrações que ainda dependem do provider registry legado
- reduzir dependência de `app/adapters/*`

### Fase 4 — Isolar ainda mais

- introduzir flags ou barreiras adicionais para impedir uso acidental do legado
- manter testes de quarentena até a migração concluir

### Fase 5 — Remover com segurança

- remover provider registry legado e adapters somente depois de confirmar ausência de chamadas
- remover surfaces antigas apenas com cobertura de testes e verificação de consumidores

## Itens que podem ser removidos já

Com a evidência atual, **nenhum componente de runtime deve ser removido sem migração adicional**.

O que pode ser tratado agora é apenas limpeza documental pontual:

- descrições antigas do README já foram ajustadas para refletir o caminho canônico
- futuras referências duplicadas em docs legadas podem ser marcadas como históricas, sem apagar o conteúdo ainda

## Itens que devem ficar deprecated

Devem continuar explicitamente deprecated/legacy:

- `app/adapters/executor_local.py`
- `app/adapters/executor_ssh.py`
- `app/adapters/docker.py`
- `app/services/provider_registry.py`
- `app/services/orchestrator.py`
- `app/api/routes.py`
- `GET /v1/providers/status`
- quaisquer docs antigos sobre o fluxo clássico de orquestração

## Itens que precisam de migração futura

- surfaces clássicas de chat e tarefas
- provider registry histórico
- adapters legados
- qualquer integração externa que ainda consuma `/v1/providers/status`
- documentação operacional que ainda descreve o fluxo clássico sem distinguir claramente o caminho canônico

## Checklist final antes de declarar o legado removido

1. Nenhuma rota clássica é consumida por produção.
2. Nenhum import do provider registry legado permanece no caminho oficial.
3. Nenhum adapter legado é referenciado pelo runner oficial.
4. `GET /v1/providers/status` foi desativado ou substituído com compatibilidade planejada.
5. Testes cobrem o caminho canônico e a ausência de dependência do legado.
6. Ferramentas de observabilidade confirmam ausência de tráfego para surfaces antigas.
7. Documentação já não orienta novos usuários ao caminho legado.
8. O novo runner v1 cobre todos os casos read-only aprovados.
9. Existe um plano de rollback caso a remoção revele dependências ocultas.

## Evidências de auditoria

Comandos de auditoria executados nesta sessão:

- `grep -RInE '/root/homelab/aiops|homelab-aiops|/opt/aiops/' . || true`
- `grep -RInE 'executor_local|executor_ssh|app.adapters.docker|provider_registry|create_subprocess_shell' app docs tests || true`
- `grep -RInE 'orchestrator|legacy|deprecated|provider|providers/status' app docs tests README.md || true`
- `find . -maxdepth 4 -type f | sort`

Validações executadas:

- `find scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n`
- `bash scripts/validate_actions_catalog.sh`
- `docker compose -f deploy/docker-compose.yml config`
- `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.bluegreen.yml config`
- `python3 -m pytest -q`
- `git diff --check`

## Resultado resumido

- O AIOps novo já é o caminho canônico para diagnóstico, planeamento, approvals, run read-only, audit e history
- O AIOps antigo ainda existe e continua vivo em surfaces clássicas e no provider registry
- A substituição é real, mas ainda parcial
- A remoção completa do legado ainda não é segura

