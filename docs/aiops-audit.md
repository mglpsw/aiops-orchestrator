# AIOps Audit

## Estado atual

O repo `aiops-orchestrator` ja opera como um orquestrador de tarefas orientado por LLM, mas hoje ele mistura tres coisas no mesmo fluxo: diagnostico/classificacao, planejamento e execucao real de comandos. O caminho principal passa por `POST /v1/chat/ingest`, gera um plano, valida por policy e, se o risco permitir, chama executores que podem abrir shell local, SSH ou Docker.

Isso significa que o estado atual nao e "diagnostico somente". Ele ainda contem uma superficie de automacao operacional real, com risco de alteracao de host, container e rede.

Resumo do comportamento atual:

- `GET /health` existe e e publico.
- `GET /ready` existe e e publico.
- `GET /metrics` existe e e publico.
- `POST /v1/chat/ingest` entra no pipeline de classificacao, planejamento e possivel execucao.
- `GET /v1/tasks`, `GET /v1/tasks/{task_id}`, `GET /v1/approvals`, `POST /v1/approvals/{task_id}`, `GET /v1/providers/status` existem como rotas autenticadas.
- Nao encontrei `GET /readyz`, `GET /v1/metrics/query`, `GET /v1/models` ou `GET /v1/route` neste repo.

## Arquivos encontrados

### Core da API

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `app/main.py` | Bootstrap FastAPI, auth middleware, CORS, health/readiness | Core da API | Manter |
| `app/api/auth.py` | Token auth por middleware | Core de seguranca | Manter e endurecer |
| `app/api/metrics.py` | Exporta metricas Prometheus | Core observability | Manter, com cuidado de exposicao |
| `app/api/routes.py` | Rotas de chat, tarefas, aprovacoes e providers | Core, mas mistura diagnostico e execucao | Refatorar e isolar fluxo de execucao |

### Orquestracao e politica

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `app/services/orchestrator.py` | Classifica, planeja, valida e executa | Core, alto risco | Refatorar fortemente |
| `app/services/task_service.py` | CRUD, auditoria, metricas de tarefas | Core persistencia | Manter |
| `app/services/provider_registry.py` | Seleciona LLMs e executores | Core de roteamento | Manter, mas separar execucao do modo diagnostico |
| `app/policies/engine.py` | Denylist, risco, approval gate, allowlist | Core de seguranca | Manter, mas reescrever partes criticas |

### Modelos e persistencia

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `app/models/database.py` | Tabelas SQLite e engine async | Core persistencia | Manter |
| `app/models/schemas.py` | Schemas Pydantic de task, approval, plan, metrics | Core contrato | Manter, com extensao para findings diagnosticos |

### Adapters de LLM e execucao

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `app/adapters/base.py` | Interfaces de LLM e executor | Core de infraestrutura | Manter |
| `app/adapters/ollama.py` | LLM local via HTTP | Dependencia externa local | Manter |
| `app/adapters/claude.py` | LLM via Anthropic | Dependencia externa | Manter |
| `app/adapters/openai_compatible.py` | LLM OpenAI-compatible | Dependencia externa | Manter |
| `app/adapters/codex.py` | Variante OpenAI voltada a code/infra | Experimental/alto uso operacional | Isolar |
| `app/adapters/executor_local.py` | `asyncio.create_subprocess_shell` local | Risco de seguranca alto | Isolar do v1 |
| `app/adapters/executor_ssh.py` | `ssh` via shell remoto | Risco de seguranca muito alto | Isolar do v1 |
| `app/adapters/docker.py` | `docker` via shell | Risco de seguranca alto | Isolar do v1 |

### Utilitarios

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `app/utils/logging.py` | Log estruturado | Core suporte | Manter |
| `app/utils/secrets.py` | Mascara segredos | Core suporte de seguranca | Manter |

### Configuracao

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `config/policies.yml` | Allow/deny e alvos protegidos | Seguranca critica | Manter e validar melhor |
| `config/providers.yml` | Roteamento de providers e executores | Core de configuracao | Manter, mas separar providers de execucao |
| `config/routes.yml` | Rotas por role/provider | Core de configuracao | Manter |
| `.env.example` | Template de segredos | Suporte | Manter |
| `.env` | Segredos locais | Sensivel | Nao versionar, nao expor |

### Testes

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `tests/test_api_middleware.py` | Auth e CORS | Teste util | Preservar |
| `tests/test_policy_engine.py` | Bloqueio de comandos perigosos | Teste util de seguranca | Preservar e ampliar |

### Docs e scripts

| Arquivo | Papel atual | Classificacao | Decisao |
|---|---|---|---|
| `README.md` | Descricao geral | Docs, com endpoints defasados | Atualizar depois |
| `docs/ARCHITECTURE.md` | Fluxo conceitual atual | Docs util, mas orientada a execucao | Manter como legado |
| `docs/SECURITY.md` | Resumo de seguranca | Docs util, mas depende do fluxo antigo | Manter como referencia |
| `docs/INTEGRATIONS.md` | Integracoes com WebUI, Prometheus, etc. | Docs util, mas bem atrelada a homelab | Isolar do v1 |
| `docs/OPERATIONS.md` | Operacao no homelab | Fora do escopo do v1 | Isolar |
| `docs/ROLLBACK.md` | Rollback operacional | Fora do escopo do v1 | Isolar |
| `docs/TROUBLESHOOTING.md` | Troubleshooting do homelab | Fora do escopo do v1 | Isolar |
| `scripts/install.sh` | Instala em CT/Proxmox | Infra/homelab | Nao usar como caminho do v1 |
| `scripts/backup.sh` | Backup na CT | Infra/homelab | Nao usar como caminho do v1 |
| `scripts/rollback.sh` | Stop/restore de servico | Infra/homelab | Nao usar como caminho do v1 |
| `scripts/validate.sh` | Valida ambiente Proxmox/CT | Infra/homelab | Nao usar como caminho do v1 |
| `scripts/smoke_test.sh` | Smoke test com host real | Infra/homelab | Nao usar como caminho do v1 |
| `scripts/migrate_savings_to_sqlite.py` | Migração auxiliar | Legado utilitario | Isolar |

## Endpoints relacionados

### Endpoints estaveis

- `GET /health`
- `GET /ready`
- `GET /metrics`

Esses endpoints sao pequenos, previsiveis e nao disparam execucao. Sao bons candidatos a permanecer como superficie base da API.

### Endpoints experimentais ou de operacao

- `POST /v1/chat/ingest`
- `GET /v1/tasks`
- `GET /v1/tasks/{task_id}`
- `GET /v1/approvals`
- `POST /v1/approvals/{task_id}`
- `GET /v1/providers/status`

Esses endpoints dependem do pipeline de LLM e, no caso de approval/ingest, podem levar a execucao real. Devem ser tratados como superficie operacional, nao como diagnostico puro.

### Endpoints duplicados ou legados

- A documentacao menciona `/v1/task` e `/v1/providers`, mas o codigo atual expoe `/v1/tasks` e `/v1/providers/status`.
- O repo usa `/ready`, nao `/readyz`.
- O repo nao expoe `/v1/metrics/query`, apesar do objetivo futuro citar esse caminho.

### Endpoints perigosos ou que pedem aprovacao extra

- `POST /v1/chat/ingest`, porque pode derivar em execucao real.
- `POST /v1/approvals/{task_id}`, porque libera execucao posterior.
- `GET /v1/providers/status`, porque pode acionar health checks contra LLMs externos e executores.
- `GET /metrics`, se ficar exposto fora da rede interna, porque revela volume, aprovacoes e blocos.

### Endpoints sem teste direto

- Todos os endpoints `v1` acima nao tem teste dedicado de rota no repo atual.
- Nao ha teste para `GET /ready`.
- Nao ha teste para `GET /metrics`.
- Nao ha teste para qualquer futura rota de diagnostico como `/readyz` ou `/v1/metrics/query`.

## Riscos de seguranca

### 1. Execucao real de shell

- `app/adapters/executor_local.py` usa `asyncio.create_subprocess_shell`.
- `app/adapters/executor_ssh.py` monta e executa um comando `ssh` via shell.
- `app/adapters/docker.py` monta e executa `docker ...` via shell.

Isto significa que o sistema ainda possui uma superficie de comando real, nao apenas simulacao.

### 2. Comando livre vindo do plano

- `app/services/orchestrator.py` pega `command` de `plan.steps[*].args.command`.
- Se o LLM gerar um comando ruim, o executor tenta rodar.
- A protecao atual depende de regex/patterns, nao de allowlist estrutural robusta.

### 3. Dry-run nao e forcado

- O executor aceita `dry_run`, mas o fluxo atual passa `dry_run=args.get("dry_run", False)`.
- Isso significa que, se o plano nao marcar dry-run, o sistema tende a executar de verdade.

### 4. Autorizacao e policy ainda sao insuficientes para execucao geral

- Existe allowlist de usuarios e role admin, o que e bom.
- Mas a autorizacao ainda esta acoplada ao fluxo de planejamento e execucao.
- O bloqueio principal continua baseado em regex e strings de comando.

### 5. Segredos e rede

- `app/core/config.py` le `.env` e variaveis de ambiente.
- Os adapters falham e fazem chamadas externas para Ollama, Anthropic e OpenAI-compatible endpoints.
- `GET /metrics` expoe sinais operacionais que podem ser sensiveis.

### 6. Mistura de diagnostico com execucao

- O mesmo fluxo que classifica e diagnostica tambem aprova e executa.
- Para um AIOps Diagnostic Engine v1, isso e o principal risco conceitual e tecnico.

## O que manter

- `app/main.py` como bootstrap da API.
- `app/api/auth.py` como base de autenticacao, com endurecimento posterior.
- `app/api/metrics.py` como telemetria basica.
- `app/models/database.py` e `app/services/task_service.py` como trilha de auditoria e persistencia.
- `app/policies/engine.py` como base de seguranca, porque o conteudo de denylist e allowlist e util.
- `app/adapters/ollama.py`, `app/adapters/claude.py`, `app/adapters/openai_compatible.py` como providers de LLM.
- `app/utils/logging.py` e `app/utils/secrets.py`.
- `tests/test_api_middleware.py` e `tests/test_policy_engine.py`.

## O que isolar

- `app/services/orchestrator.py`, porque hoje ele e o ponto em que diagnostico vira execucao.
- `app/adapters/executor_local.py`, `app/adapters/executor_ssh.py`, `app/adapters/docker.py`, porque sao superficie de comando real.
- `app/adapters/codex.py`, porque puxa o sistema para automacao de codigo/infra e nao para diagnostico.
- `app/api/routes.py` como superficie v1 operacional, ate existir um caminho separado para diagnostico puro.
- `scripts/*.sh`, `docs/OPERATIONS.md`, `docs/ROLLBACK.md`, `docs/TROUBLESHOOTING.md`, `docs/INTEGRATIONS.md`, porque sao orientados ao homelab/infra e nao ao futuro v1 diagnostico.

## O que remover depois

Nao remover agora. Mas, depois que o AIOps Diagnostic Engine v1 existir, estes pontos devem ser candidatos a saida do fluxo principal:

- execucao local de shell generico;
- execucao SSH generica;
- execucao Docker generica;
- endpoints que disparam aprovacao para executar comandos;
- docs antigas que falam em `/v1/task` singular e em fluxos de execucao direta;
- qualquer caminho onde o plano do LLM possa carregar `command` livre e chegar no executor sem uma allowlist especifica.

## O que refatorar

- Separar diagnostico de execucao no nivel de dominio, nao so no nivel de endpoint.
- Tirar o campo `command` livre do fluxo diagnostico v1.
- Trocar o modelo de saida do v1 para `findings`, `severity` e `recommended_actions`, em vez de `steps` executaveis.
- Tornar `dry_run` o padrao absoluto no futuro, e nunca implicito.
- Introduzir allowlist por tipo de acao, nao so regex de bloqueio.
- Reescrever a validacao para distinguir leitura, analise, simulacao e alteracao real.
- Adicionar teste de bloqueio para shell real e para comandos sensiveis.
- Adicionar rotas de compatibilidade sem quebrar `/health`, `/ready` e `/metrics`.

## Testes existentes

O repo hoje tem apenas dois arquivos de teste:

- `tests/test_api_middleware.py`
- `tests/test_policy_engine.py`

O que eles cobrem hoje:

- autenticacao obrigatoria em rota protegida;
- `/health` publico;
- CORS para origem local;
- bloqueio de `rm -rf /` e variantes;
- classificacao de `rm -rf /tmp/scratch` como high risk;
- classificacao de `pct start 102` como high risk.

No total, o `pytest -q` passou com `6 passed`.

## Lacunas de teste

- Nao existe teste de `POST /v1/chat/ingest`.
- Nao existe teste de `GET /v1/tasks` nem de aprovacao.
- Nao existe teste de `GET /ready` nem de `GET /metrics`.
- Nao existe teste de `LocalExecutorAdapter`, `SSHExecutorAdapter` ou `DockerAdapter` com dry-run.
- Nao existe teste que prove que shell real e bloqueado no fluxo diagnostic-only.
- Nao existe teste de schema para findings/severity/recommended_actions.
- Nao existe teste de compatibilidade com `readyz` ou `v1/metrics/query`.
- Nao achei testes nem codigo para `terminal_agent`.

## Recomendacao para AIOps Diagnostic Engine v1

O v1 deve ser um motor de diagnostico somente, com saida estruturada e sem execucao real.

Direcao recomendada:

- consumir estado por `GET /ready` hoje, com previsao de alias `GET /readyz` se necessario para compatibilidade;
- consumir metricas por uma interface de consulta, idealmente `GET /v1/metrics/query` no futuro, ou uma adaptacao equivalente sobre `GET /metrics` enquanto a API nao tiver o novo endpoint;
- produzir somente `findings`, `severity` e `recommended_actions`;
- manter `dry_run` como unico modo permitido no v1;
- nao aceitar `command` livre como saida principal do diagnostico;
- tratar qualquer execucao futura como pipeline separado, allowlisted e com approval explicito;
- preservar `/health`, `/ready`, `/metrics` e a autenticacao atual sem quebrar o resto da API.

Em outras palavras: o v1 deve diagnosticar, nao remediar.

## Plano incremental sugerido

1. Criar uma camada de diagnostico separada do `Orchestrator` atual.
2. Reusar `TaskService`, auditoria e autenticacao, mas nao os executores reais.
3. Definir um schema de saida para findings com `severity` e `recommended_actions`.
4. Mapear `ready` e metricas como entradas do diagnostico.
5. Congelar execucao real em uma interface separada, com allowlist e approval independentes.
6. Adicionar testes de bloqueio para shell real e para qualquer tentativa de remediacao automatica no v1.
7. Só depois disso considerar uma rota de execucao futura, distinta do caminho diagnostico.

## Verificacao realizada

- `git status --short`
- `git log --oneline -8`
- `find . -maxdepth 4 -type f | sort`
- `pytest -q`
- `python3 -m compileall app tests` ainda nao executado no momento de escrever este relatorio; foi mantido para a validacao final.

