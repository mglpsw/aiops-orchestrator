# AIOps Orchestrator — Catálogo de Actions

## Visão geral

O catálogo de actions (`config/actions.yaml`) define o conjunto explícito e allowlisted de operações
que o Action Planner pode sugerir. Nenhum comando livre é aceito fora deste catálogo.

**Fase atual (v1):** apenas ações `mode: readonly` e `risk: low` estão no catálogo.
Ações de escrita, restart, deploy ou remediação não existem nesta fase.

---

## Schema de uma action

```yaml
- action_id: string          # identificador único, snake_case
  description: string        # descrição legível por humanos
  command: string            # comando exato — sem interpolação livre
  mode: readonly             # readonly | readwrite (v1: somente readonly)
  risk: low                  # low | medium | high
  timeout_seconds: integer   # limite de execução em segundos
  requires_approval: boolean # true = aprovação humana obrigatória antes de executar
  tags: [string]             # categorias para filtragem e auditoria
```

### Campos obrigatórios

Todos os campos acima são obrigatórios. A ausência de qualquer um invalida a action e bloqueia o
deploy (validado por `scripts/validate_actions_catalog.sh`).

### Regras do catálogo

- `action_id` deve ser único no catálogo
- `mode: readonly` é o único valor permitido no v1
- `risk: low` é o único valor permitido no v1
- Nenhum comando pode conter padrões bloqueados (ver abaixo)
- Nenhuma interpolação livre de variáveis de usuário é permitida

### Padrões de comando bloqueados (validados automaticamente)

| Categoria                        | Motivo do bloqueio                    |
| -------------------------------- | ------------------------------------- |
| remoção destrutiva               | Remoção de arquivos/diretórios        |
| permissão 777                    | Permissão irrestrita                  |
| execução dentro de container     | Execução dentro de container          |
| acesso remoto por SSH            | Acesso remoto sem allowlist           |
| pipe para shell                  | Execução remota de código (RCE)       |
| pipeline para shell              | Pipe para shell (RCE)                 |
| pipeline para shell secundário   | Pipe para shell (RCE)                 |
| push remoto de repositório       | Alteração de repositório remoto       |
| compose startup                  | Alteração de stack Docker             |
| reinício de serviço              | Reinício de serviço                   |
| início de serviço                | Início de serviço                     |
| parada de serviço                | Parada de serviço                     |
| desabilitação de serviço         | Desabilitação de serviço              |

---

## Actions disponíveis no v1

### git_status

Exibe o estado atual da árvore de trabalho do repositório canônico.

- **Comando:** `git -C /opt/aiops-orchestrator status`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### git_diff_stat

Exibe estatísticas de diff em relação ao HEAD (sem patch completo).

- **Comando:** `git -C /opt/aiops-orchestrator diff --stat HEAD`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### git_log_recent

Exibe os 10 commits mais recentes em formato compacto.

- **Comando:** `git -C /opt/aiops-orchestrator log --oneline -10`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### docker_compose_config

Valida e exibe a configuração final do docker-compose (sem iniciar nada).

- **Comando:** `docker compose -f /opt/aiops-orchestrator/deploy/docker-compose.yml config`
- **Risk:** low | **Mode:** readonly | **Timeout:** 15s | **Approval:** false

### systemctl_status_aiops

Exibe o status da unit systemd do aiops-orchestrator (se gerenciado via systemd).

- **Comando:** `systemctl status aiops-orchestrator --no-pager`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_health_8000

Verifica o endpoint `/health` da produção estável (porta 8000).

- **Comando:** `curl -fsS http://127.0.0.1:8000/health`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_ready_8000

Verifica o endpoint `/ready` da produção estável (porta 8000).

- **Comando:** `curl -fsS http://127.0.0.1:8000/ready`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_health_8001

Verifica o endpoint `/health` do runtime next/observe (porta 8001).

- **Comando:** `curl -fsS http://127.0.0.1:8001/health`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_ready_8001

Verifica o endpoint `/ready` do runtime next/observe (porta 8001).

- **Comando:** `curl -fsS http://127.0.0.1:8001/ready`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### journalctl_aiops_recent

Exibe as últimas 50 linhas do journal do aiops-orchestrator.

- **Comando:** `journalctl -u aiops-orchestrator -n 50 --no-pager`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### prometheus_query

Consulta o endpoint de query do Prometheus para a métrica `up` (health check básico).

- **Comando:** `curl -fsS 'http://192.168.3.200:9090/api/v1/query?query=up'`
- **Risk:** low | **Mode:** readonly | **Timeout:** 15s | **Approval:** false

---

## Validação do catálogo

### Validação no startup (runtime)

O catálogo é validado automaticamente durante o startup da aplicação via
`init_catalog_on_startup()` (chamado pelo lifespan em `app/main.py`).

**O que acontece no startup:**

| Situação | Resultado |
| -------- | --------- |
| Catálogo válido | Cache populado, estado `ok`, log `INFO: Action catalog loaded: N actions` |
| Catálogo inválido | Cache vazio, estado `error`, log `ERROR: Action catalog failed to load`, readiness degradada |
| Arquivo ausente | Idem — catálogo inválido |

**Estado do catálogo no `/ready`:**

```json
{
  "status": "not_ready",
  "checks": { "action_catalog": false },
  "dependencies": {
    "action_catalog": {
      "status": "error",
      "actions_count": 0
    }
  }
}
```

O catálogo é cacheado em memória após o primeiro carregamento bem-sucedido.
Recarregamentos por requisição não ocorrem — o cache só é substituído num novo startup.

### Validação antes do commit

Execute antes de qualquer commit que altere `config/actions.yaml`:

```bash
bash scripts/validate_actions_catalog.sh
```

O script verifica:

1. YAML parseável
2. `action_id` único no catálogo
3. Campos obrigatórios presentes e não-nulos
4. Nenhum padrão de comando bloqueado nos campos `command`

---

## Como adicionar uma nova action (futuro)

1. Definir `action_id` único e descritivo
2. Preencher todos os campos obrigatórios
3. Garantir que `command` não contenha padrões bloqueados
4. Para v1: manter `mode: readonly` e `risk: low`
5. Executar `scripts/validate_actions_catalog.sh` — deve passar sem erros
6. Abrir PR com justificativa de negócio para a nova action
7. Revisão humana obrigatória antes de merge

**Não adicionar actions de escrita, restart, deploy ou remediação sem aprovação explícita
do owner do repositório e atualização da fase (v1 → v2+).**

---

## Action Planner

O Action Planner (`app/services/action_planner.py`) é a camada que mapeia `action_ids` explícitos
para um plano estruturado e seguro, sem envolver LLM ou comando livre.

### Endpoints

| Método | Path | Descrição |
| ------ | ---- | --------- |
| `GET` | `/v1/aiops/actions/catalog` | Lista o catálogo allowlisted (sem expor comandos) |
| `POST` | `/v1/aiops/actions/plan` | Gera um plano determinístico a partir de `action_ids` |
| `POST` | `/v1/aiops/actions/dry-run` | Simula um plano allowlisted sem executar nada |
| `GET` | `/v1/aiops/audit/recent` | Retorna os eventos auditados mais recentes |

Ambos os endpoints requerem autenticação Bearer e retornam `dry_run: true`.

### Contrato do planner

**Request (`POST /v1/aiops/actions/plan`):**

```json
{
  "target": "agent-router",
  "action_ids": ["git_status", "curl_health_8000"],
  "context": "Diagnóstico de readiness falhou",
  "dry_run": true
}
```

**Response:**

```json
{
  "plan_id": "<uuid>",
  "target": "agent-router",
  "status": "ready",
  "risk": "low",
  "requires_approval": false,
  "steps": [
    {
      "action_id": "git_status",
      "title": "Exibe o estado atual da árvore de trabalho do repositório canônico",
      "risk": "low",
      "mode": "readonly",
      "requires_approval": false,
      "reason": "Selected from validated read-only action catalog",
      "evidence_source": "Diagnóstico de readiness falhou",
      "finding_id": null
    }
  ],
  "blocked_steps": [],
  "warnings": [],
  "dry_run": true
}
```

### Comportamento fail-closed

| Situação | Resultado |
| -------- | --------- |
| `action_id` não existe no catálogo | `blocked_steps` |
| `action_id` com `mode != readonly` | `blocked_steps` (policy gate) |
| `action_id` com `risk != low` | `blocked_steps` (policy gate) |
| `action_ids` vazio | `status: empty` |
| Todos bloqueados | `status: blocked` |
| Catálogo ausente ou inválido | HTTP 503 |
| `dry_run: false` na request | HTTP 422 |

### Garantias do planner

- Nenhum `command` é incluído na resposta do plano
- Nenhuma string livre é aceita como `action_id` — apenas IDs do catálogo
- O plano não dispara execução real
- `dry_run` é sempre `true` na resposta
- `plan_id` é único por chamada (UUID v4)
- O planner é determinístico e testável sem LLM

### Audit Log

As operações de planejamento e simulação escrevem eventos estruturados em JSONL.

- Caminho padrão: `var/audit/aiops_audit.jsonl`
- Configuração:
  - `AIOPS_AUDIT_LOG_PATH`
  - `AIOPS_AUDIT_LOG_REQUIRED=true|false`
  - `AIOPS_AUDIT_LOG_MAX_BYTES`
  - `AIOPS_AUDIT_LOG_BACKUP_COUNT`
  - `AIOPS_AUDIT_LOG_ROTATION_ENABLED=true|false`
- Eventos registrados:
  - `action_plan_created`
  - `action_dry_run_created`
  - `diagnose_action_plan_attached`
- Campos gravados:
  - `event_id`
  - `timestamp`
  - `target`
  - `source_endpoint`
  - `plan_id`
  - `dry_run_id`
  - `risk`
  - `status`
  - `action_ids`
  - `blocked_action_ids`
  - `warnings_count`
  - `blocked_steps_count`
- Nenhum comando, segredo ou cabeçalho sensível é persistido
- `GET /v1/aiops/audit/recent` expõe apenas os eventos mais recentes, com limite máximo de 100
- A retenção é por rotação simples do arquivo ativo, com backups numerados e limite configurável

---

## Dry-run simulation

O endpoint `POST /v1/aiops/actions/dry-run` simula um plano allowlisted sem executar comandos.
Ele reutiliza o Action Planner e o catálogo validado no startup, mas converte a saída em uma
simulação explícita com `would_run`, `blocked_steps` e `warnings`.

### Endpoint

| Método | Path | Descrição |
| ------ | ---- | --------- |
| `POST` | `/v1/aiops/actions/dry-run` | Simula um plano allowlisted sem executar nada |

### Request

```json
{
  "target": "agent-router",
  "action_ids": ["curl_health_8000", "curl_ready_8000"],
  "reason": "Investigate degraded health score",
  "dry_run": true
}
```

### Response

```json
{
  "dry_run_id": "dryrun_5e7c4e1f0b7d4c0a",
  "target": "agent-router",
  "status": "ok",
  "risk": "low",
  "requires_approval": false,
  "plan": {
    "plan_id": "<uuid>",
    "target": "agent-router",
    "status": "ready",
    "risk": "low",
    "requires_approval": false,
    "steps": [
      {
        "action_id": "curl_health_8000",
        "title": "Verifica o endpoint /health da produção estável (porta 8000)",
        "risk": "low",
        "mode": "readonly",
        "requires_approval": false,
        "reason": "Selected from validated read-only action catalog",
        "evidence_source": "Investigate degraded health score",
        "finding_id": null
      }
    ],
    "blocked_steps": [],
    "warnings": [],
    "dry_run": true
  },
  "would_run": [
    {
      "action_id": "curl_health_8000",
      "title": "Verifica o endpoint /health da produção estável (porta 8000)",
      "mode": "readonly",
      "risk": "low",
      "requires_approval": false,
      "execution": "not_executed",
      "reason": "Dry-run simulation only"
    }
  ],
  "blocked_steps": [],
  "warnings": [
    "Dry-run simulation only; no commands were executed."
  ]
}
```

### Regras

- `dry_run` é obrigatório e deve ser `true`
- `command` no payload é rejeitado com HTTP 422
- `would_run[].execution` é sempre `not_executed`
- `would_run` é derivado do `ActionPlanResponse`, sem executar nada
- `blocked_steps` preserva action_id desconhecido, inválido ou fora do catálogo
- Catálogo indisponível retorna HTTP 503, assim como `/catalog` e `/plan`
- `status`:
  - `ok`: todos os passos válidos e nenhum `blocked_step`
  - `partial`: há passos válidos e `blocked_steps`
  - `blocked`: nenhum passo válido ou catálogo indisponível

### Garantias

- Nenhum `command` é incluído na resposta
- Nenhuma execução real ocorre
- Nenhum shell, SSH, Docker, `git`, `curl` real ou `systemctl` é chamado
- O endpoint é autenticado por Bearer token como os demais endpoints sensíveis

---

## Integração com Diagnose

O endpoint `POST /v1/aiops/diagnose` passa a incluir um campo `action_plan` no response quando o
diagnóstico detecta problemas (findings com status `critical`, `warning`, `degraded`, `not_ready`
ou `down`).

O `health_score` do diagnose é calculado antes do planner e não executa ações. Ele apenas
acompanha o estado operacional do diagnóstico. Já `recommended_action_ids` nos findings continuam
sendo apenas insumos determinísticos para o Action Planner transformar em `action_plan`.

### Módulo de mapeamento

`app/agent_router/services/action_mapper.py` é o único lugar que mapeia check names / signal names
para `action_ids`. Sem LLM, sem texto livre, sem interpolação.

**Tabela de mapeamento:**

| Check / Signal | action_ids sugeridos |
| -------------- | -------------------- |
| `readiness` | `curl_health_8000`, `curl_ready_8000`, `systemctl_status_aiops` |
| `backend_up` | `curl_health_8000`, `curl_ready_8000` |
| `error_rate` | `journalctl_aiops_recent`, `prometheus_query` |
| `latency_p95` | `prometheus_query`, `journalctl_aiops_recent` |
| `blocked_tasks` | `journalctl_aiops_recent` |
| `model_selection` | `journalctl_aiops_recent` |
| `ollama_models_count` | `journalctl_aiops_recent` |
| *(qualquer problema)* | + `git_status`, `git_log_recent` (gerais) |

### Contrato do campo `action_plan` no diagnose response

```json
{
  "status": "critical",
  "severity": "high",
  "summary": "...",
  "signals": [...],
  "findings": [...],
  "recommended_actions": [...],
  "dry_run": true,
  "action_plan": {
    "plan_id": "<uuid>",
    "target": "agent-router",
    "status": "ready",
    "risk": "low",
    "requires_approval": false,
    "steps": [
      {
        "action_id": "curl_health_8000",
        "title": "Verifica o endpoint /health da produção estável (porta 8000)",
        "risk": "low",
        "mode": "readonly",
        "requires_approval": false,
        "reason": "Selected from validated read-only action catalog",
        "evidence_source": "diagnose status=critical severity=high",
        "finding_id": null
      }
    ],
    "blocked_steps": [],
    "warnings": [],
    "dry_run": true
  }
}
```

### Comportamento fail-soft

| Situação | Resultado no diagnose |
| -------- | --------------------- |
| Nenhum finding com problema | `action_plan: null` |
| Status `ok` | `action_plan: null` |
| Catálogo ausente / inválido | `action_plan: null` (diagnose retorna 200) |
| Finding sem signal em evidence | fallback para check names do request |
| action_id mapeado não está no catálogo | vai para `blocked_steps` (planner fail-closed) |

### Garantias da integração

- `action_plan` nunca contém campo `command`
- `action_plan.dry_run` é sempre `true`
- Falha no catálogo não retorna HTTP 5xx ao cliente (fail-soft no diagnose)
- Todos os campos originais do diagnose são preservados
- O mapeamento é determinístico e não usa LLM
- `recommended_action_ids` não executam nada; apenas alimentam o planner
