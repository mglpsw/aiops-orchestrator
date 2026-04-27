# AIOps Orchestrator — Project Status

## Objetivo

O objetivo do AIOps Orchestrator é operar como um orquestrador seguro e auditável para diagnóstico,
planejamento e execução read-only allowlisted. O foco atual é reduzir risco operacional mantendo
sempre:

- autenticação explícita;
- allowlist estrutural;
- approval gate;
- auditoria persistente;
- redaction de segredos;
- fail-closed em catálogo, planner, dry-run, approval e run.

## Status canônico atual

Esta é a fotografia canônica do projeto antes da próxima fase:

- o caminho produtivo é diagnóstico + planejamento + simulação + aprovação + execução read-only;
- o runner oficial não aceita shell livre;
- o catálogo é estrutural e validado no startup;
- o histórico de runs e o audit log já existem;
- o diagnóstico agora retorna findings enriquecidos com `impact`, `probable_cause`, `confidence`, `next_validation` e `recommended_action_ids`;
- o health score passou a ser severity-aware e a aceitar baseline temporal simples via `metadata` quando há dados;
- o GitHub Agent Review está disponível para revisão on-demand de PRs;
- o Agent Router pode ser usado opcionalmente apenas como API de revisão LLM, nunca como executor.
- o chat compatível com OpenWebUI detecta intents AIOps determinísticas e roteia para diagnóstico, runs, approvals e status sem executar actions;

## Arquitetura atual

### Camadas principais

- API FastAPI em `app/main.py`
- diagnóstico determinístico em `app/agent_router/services/aiops_diagnostic.py`
- action catalog em `app/services/action_catalog.py`
- action planner em `app/services/action_planner.py`
- dry-run em `app/agent_router/services/action_dry_run.py`
- approval store em `app/agent_router/services/approval_store.py`
- audit log em `app/agent_router/services/audit_log.py`
- run history em `app/agent_router/services/run_store.py`
- read-only runner allowlisted em `app/agent_router/services/action_runner.py`

### Fluxo canônico

```text
diagnose -> plan -> dry-run -> approval -> run -> run history
```

O fluxo é sempre read-only nesta fase. O run executa apenas funções internas fixas e allowlisted.

## Endpoints existentes

### Diagnóstico e ações

- `POST /v1/aiops/diagnose`
- `GET /v1/aiops/actions/catalog`
- `POST /v1/aiops/actions/plan`
- `POST /v1/aiops/actions/dry-run`
- `POST /v1/aiops/actions/run`

### Aprovações

- `POST /v1/aiops/actions/approvals`
- `GET /v1/aiops/actions/approvals/{approval_id}`
- `POST /v1/aiops/actions/approvals/{approval_id}/approve`
- `POST /v1/aiops/actions/approvals/{approval_id}/reject`

### Histórico e auditoria

- `GET /v1/aiops/runs/recent`
- `GET /v1/aiops/runs/{run_id}`
- `GET /v1/aiops/audit/recent`

### Saúde e compatibilidade

- `GET /health`
- `GET /ready`
- `GET /metrics`
- endpoints legados de chat, tasks e providers ainda existem por compatibilidade histórica

## Stores existentes

- audit store JSONL em `var/audit/aiops_audit.jsonl`
- approval store JSONL em `var/approvals/aiops_approvals.jsonl`
- run history JSONL em `var/runs/aiops_runs.jsonl`

Esses stores guardam metadados seguros. Eles não persistem `command`, `argv`, tokens, headers ou
payloads brutos sensíveis.

## Actions disponíveis no catálogo

O catálogo atual em `config/actions.yaml` contém 13 actions allowlisted:

- `curl_health_8000`
- `curl_ready_8000`
- `curl_health_8001`
- `curl_ready_8001`
- `git_status`
- `git_diff_stat`
- `git_log_recent`
- `docker_compose_config`
- `docker_compose_bluegreen_config`
- `systemctl_status_aiops`
- `journalctl_aiops_recent`
- `prometheus_query`
- `prometheus_query_allowlisted`

O catálogo é validado no startup e permanece fail-closed se estiver ausente ou inválido.

## Actions read-only executáveis atuais

O runner oficial executa apenas as funções internas fixas abaixo:

- `curl_health_8000`
- `curl_ready_8000`
- `curl_health_8001`
- `curl_ready_8001`
- `git_status`
- `git_diff_stat`
- `docker_compose_config`
- `docker_compose_bluegreen_config`
- `systemctl_status_aiops`
- `journalctl_aiops_recent`
- `prometheus_query_allowlisted`

Nesta Session 14, o contrato que estamos fechando de forma mais explícita é o de
`git_diff_stat` e `docker_compose_bluegreen_config`: ambos permanecem read-only,
com `shell=False`, `argv` fixo, `cwd` canônico, timeout obrigatório, redaction e
output truncado.

Nesta Session 15, o contrato que estamos fechando de forma mais explícita é o de
`systemctl_status_aiops` e `journalctl_aiops_recent`: o primeiro é só leitura do
estado da unit permitida; o segundo é logs bounded, sem filtros livres, sem
follow e com janela fixa.

Nesta Session 16, o contrato que estamos fechando de forma mais explícita é o de
`prometheus_query_allowlisted`: ele aceita somente queries IDs allowlisted, usa
base URL segura existente, mantém timeout obrigatório e redige respostas e erros.

Garantias principais:

- `shell=False`
- argv fixo
- cwd canônico allowlisted
- env sanitizado
- timeout obrigatório
- output truncado e redigido
- sem `git push`, `git pull`, `git checkout`, `git reset`, `docker exec`, `docker compose up/down/restart/pull/build`

## Integrações existentes

- GitHub Agent Review on-demand via comentário de PR com `/agent review`
- modo opcional `/agent review llm` com Agent Router, somente como API de análise/revisão
- validação blue/green via `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.bluegreen.yml config`
- auditoria estruturada para plan, dry-run, approvals e run

## Garantias de segurança preservadas

- autenticação Bearer nas superfícies sensíveis
- approval gate para execução
- fail-closed em catálogo, planner, dry-run e run
- sem shell livre
- sem SSH
- sem `docker exec`
- sem deploy automático
- sem execução de código do PR no GitHub Agent Review
- redaction forte de segredos, tokens, cookies e URLs sensíveis
- histórico e auditoria sem expor conteúdo sensível

## O que ainda não existe

Ainda não existe nesta fase:

- shell livre
- SSH
- `docker exec`
- deploy automático
- GitHub Bridge real
- Local Agent Bridge genérico
- Claude/Codex Bridge
- execução de código vindo de PR
- comando livre vindo de comentário, YAML ou LLM

## Validações esperadas

As validações canônicas para mudanças nesta área são:

```bash
python3 -m pytest -q
bash scripts/ci_validate.sh
git diff --check
find scripts -name '*.sh' -print0 | xargs -0 -n1 bash -n
bash scripts/validate_actions_catalog.sh
docker compose -f deploy/docker-compose.yml config
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.bluegreen.yml config
```

## Roadmap imediato

### Session 14

- consolidar e estabilizar a superfície canônica
- revisar redundâncias documentais e alinhar termos
- manter o runner read-only e o catálogo fail-closed

### Session 15

- endurecer observabilidade e auditoria
- revisar cobertura de redaction e histórico
- manter zero execução livre

### Session 16

- tratar migração/isolamento adicional das surfaces legadas
- reduzir dependências históricas sem quebrar compatibilidade

### Session 17

- revisar integrações opcionais já existentes, sem ampliar escopo executor
- manter Agent Router apenas como API de análise/revisão

### Session 18

- integrar o fluxo de chat/OpenWebUI ao AIOps determinístico
- atualizar docs, release notes e checkpoint final da fase
- preparar a próxima fase com fronteiras explícitas de segurança e foco em `agent-router-api`

## Resumo

O projeto está em um estado canônico de diagnóstico + planejamento + execução read-only
allowlisted, com auditoria, histórico e integração opcional com GitHub Agent Review.
O que não fizer parte desse contrato permanece fora da fase atual.

O checkpoint da Session 18 fecha a ponte de chat/OpenWebUI em modo seguro e read-only,
com intents AIOps determinísticas, respostas curtas em pt-BR e fallback para o fluxo normal
quando a mensagem não for um pedido operacional.
