# Metrics Contract — aiops-orchestrator

> **Versão:** 1.0.0
> **Data:** 2026-04-27
> **Baseado em:** auditoria ao vivo do código (app/api/metrics.py, app/agent_router/metrics.py, app/api/legacy_usage.py)

---

## Objetivo

Definir o contrato formal de métricas expostas pelo `aiops-orchestrator` em `/metrics`
para consumo pelo Prometheus do CT 200 (homelab). Este contrato governa:

- quais métricas existem e onde estão implementadas
- seus tipos, labels e cardinalidade
- o que é proibido
- exemplos PromQL e alertas recomendados

---

## Fronteira de Namespaces

| Serviço              | Namespaces de métricas               | Local canônico         |
|----------------------|--------------------------------------|------------------------|
| `aiops-orchestrator` | `aiops_*`, `agent_router_aiops_*`    | CT 102 `/opt/aiops-orchestrator` porta 8000 |
| `agent-router-api`   | `agent_router_*` (exceto `agent_router_aiops_*`) | CT 102 `/opt/agent-router-api` porta 8010 |
| Prometheus / Grafana | n/a                                  | CT 200                 |

> **Nota sobre `agent_router_aiops_*`:** as métricas de diagnóstico foram prefixadas
> com `agent_router_aiops_` por origem histórica (o diagnóstico foi implementado dentro
> do submódulo `app/agent_router/`). Elas pertencem ao `aiops-orchestrator` e são
> expostas em `:8000/metrics`. Não confundir com as métricas `agent_router_*` do
> `agent-router-api` em `:8010/metrics`.

---

## Métricas Implementadas

### 1. `aiops_tasks_total` — Gauge

Total de tarefas registradas no banco de dados.

| Campo         | Valor                                                |
|---------------|------------------------------------------------------|
| Tipo          | Gauge                                                |
| Labels        | nenhum                                               |
| Cardinalidade | n/a                                                  |
| Fonte         | `SELECT COUNT(*) FROM tasks` (via TaskService)       |
| Arquivo       | `app/api/metrics.py`                                 |

---

### 2. `aiops_tasks_by_status` — Gauge

Tarefas agrupadas por status.

| Campo         | Valor                                                |
|---------------|------------------------------------------------------|
| Tipo          | Gauge                                                |
| Labels        | `status`                                             |
| Cardinalidade | Baixa — valores definidos pelo enum `TaskStatus`     |
| Arquivo       | `app/api/metrics.py`                                 |

**Valores esperados de `status`:** `pending`, `awaiting_approval`, `approved`,
`running`, `done`, `failed`, `blocked`, `cancelled` (enum do modelo TaskRecord).

---

### 3. `aiops_provider_calls_total` — Counter¹

Total de chamadas a provedores LLM registradas no banco.

| Campo         | Valor                                                |
|---------------|------------------------------------------------------|
| Tipo          | Counter (declarado) / Gauge (comportamento real)     |
| Labels        | nenhum                                               |
| Cardinalidade | n/a                                                  |
| Arquivo       | `app/api/metrics.py`                                 |

¹ **Atenção:** o valor é derivado de `SELECT COUNT(*) FROM provider_calls`. Se registros
forem deletados, o valor pode diminuir — violando o contrato de Counter. Tratar como
Gauge em dashboards (usar valor absoluto, não `rate()`).

---

### 4. `aiops_provider_failures_total` — Counter¹

Total de falhas de provedores LLM registradas no banco.

| Campo         | Valor                                                |
|---------------|------------------------------------------------------|
| Tipo          | Counter (declarado) / Gauge (comportamento real)     |
| Labels        | nenhum                                               |
| Cardinalidade | n/a                                                  |
| Arquivo       | `app/api/metrics.py`                                 |

¹ Mesma ressalva que `aiops_provider_calls_total`.

---

### 5. `aiops_approvals_pending` — Gauge

Tarefas aguardando aprovação humana no momento.

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Gauge                                                 |
| Labels        | nenhum                                                |
| Cardinalidade | n/a                                                   |
| Fonte         | `COUNT WHERE status = 'awaiting_approval'`            |
| Arquivo       | `app/api/metrics.py`                                  |

---

### 6. `aiops_blocked_actions_total` — Counter¹

Total de ações bloqueadas pela política de segurança.

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Counter (declarado) / Gauge (comportamento real)      |
| Labels        | nenhum                                                |
| Cardinalidade | n/a                                                   |
| Arquivo       | `app/api/metrics.py`                                  |

¹ Mesma ressalva dos counters derivados de COUNT(*) SQL.

---

### 7. `agent_router_aiops_diagnose_total` — Counter

Total de requisições ao endpoint `/v1/aiops/diagnose`, por resultado.

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Counter (in-memory, correto)                          |
| Labels        | `status`, `severity`                                  |
| Cardinalidade | Baixa — enums fixos normalizados                      |
| Arquivo       | `app/agent_router/metrics.py`                         |

**Valores de `status`:** `ok` | `warning` | `critical` | `unknown`
**Valores de `severity`:** `low` | `medium` | `high`

Labels normalizados por `_normalize_label()` com allowlist explícita — sem risco de
cardinalidade explodida por valores inválidos.

---

### 8. `agent_router_aiops_diagnose_latency_seconds` — Counter (cumulativo)

Latência total acumulada de diagnósticos (em segundos).

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Counter cumulativo (não Histogram)                    |
| Labels        | `status`, `severity`                                  |
| Cardinalidade | Baixa                                                 |
| Arquivo       | `app/agent_router/metrics.py`                         |

> Para calcular latência média: `rate(agent_router_aiops_diagnose_latency_seconds[5m])
> / rate(agent_router_aiops_diagnose_total[5m])`

---

### 9. `agent_router_aiops_findings_total` — Counter

Total de findings emitidos nos diagnósticos, por severidade.

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Counter (in-memory, correto)                          |
| Labels        | `severity`                                            |
| Cardinalidade | Baixa                                                 |
| Arquivo       | `app/agent_router/metrics.py`                         |

**Valores de `severity`:** `low` | `medium` | `high`

---

### 10. `aiops_legacy_endpoint_hits_total` — Counter

Requisições a endpoints legados (deprecated), por endpoint.

| Campo         | Valor                                                 |
|---------------|-------------------------------------------------------|
| Tipo          | Counter (in-memory, correto)                          |
| Labels        | `endpoint`                                            |
| Cardinalidade | Baixa — enum fixo de 6 valores                        |
| Arquivo       | `app/api/legacy_usage.py`                             |

**Valores de `endpoint`:** `chat_ingest` | `tasks_collection` | `tasks_item` |
`approvals_collection` | `approvals_item` | `providers_status`

---

## Métricas Ausentes / Desejadas (próximas iterações)

| Métrica                                    | Tipo      | Justificativa                                         |
|--------------------------------------------|-----------|-------------------------------------------------------|
| `aiops_http_requests_total`                | Counter   | Rate de requisições por endpoint e status HTTP        |
| `aiops_http_request_duration_seconds`      | Histogram | P95/P99 de latência por endpoint                      |
| `aiops_action_run_duration_seconds`        | Histogram | Duração de runs de ações                              |
| `aiops_approval_wait_seconds`              | Histogram | Tempo entre criação e decisão de aprovação            |
| `aiops_audit_events_total`                 | Counter   | Eventos de auditoria por tipo                         |
| `aiops_active_runs`                        | Gauge     | Runs de ação em progresso agora                       |
| `aiops_provider_calls_by_provider_total`   | Counter   | Chamadas por provedor (ollama/claude/openai)           |
| `aiops_policy_decisions_total`             | Counter   | Decisões da policy engine por resultado               |

---

## Métricas Explicitamente Proibidas

**Labels proibidas em qualquer métrica `aiops_*` ou `agent_router_aiops_*`:**

| Label          | Razão                                          |
|----------------|------------------------------------------------|
| `user_id`      | Alta cardinalidade + risco de PII              |
| `user`         | Idem                                           |
| `token`        | Expõe segredo                                  |
| `prompt`       | Expõe conteúdo de prompt — PII                 |
| `session_id`   | Alta cardinalidade                             |
| `request_id`   | Alta cardinalidade                             |
| `error_message`| Pode vazar conteúdo sensível                   |
| `command`      | Expõe comandos executados — risco de segurança |
| `output`       | Expõe output de execução — risco de PII        |

**Métricas proibidas:**

- Qualquer métrica com o prefixo `agent_router_*` puro (pertence ao `agent-router-api`)
- Métricas que expõem conteúdo de diagnóstico, prompt ou resposta de LLM
- Counters de valores SQL que podem decrementar (usar Gauge explicitamente)

---

## Exemplos PromQL

```promql
# Tarefas pendentes de aprovação
aiops_approvals_pending

# Tarefas por status
aiops_tasks_by_status

# Taxa de diagnósticos com anomalia (warning+critical)
rate(agent_router_aiops_diagnose_total{status=~"warning|critical"}[5m])

# Taxa geral de diagnósticos
rate(agent_router_aiops_diagnose_total[5m])

# Latência média de diagnóstico
rate(agent_router_aiops_diagnose_latency_seconds[5m])
/ rate(agent_router_aiops_diagnose_total[5m])

# Findings por severidade (últimas 24h)
increase(agent_router_aiops_findings_total[24h])

# Taxa de falhas de provider
aiops_provider_failures_total / aiops_provider_calls_total

# Endpoints legados ainda sendo usados
aiops_legacy_endpoint_hits_total > 0

# Ações bloqueadas (anomalia se crescer)
aiops_blocked_actions_total
```

---

## Alertas Recomendados

| Nome                                  | Condição                                                              | Severidade |
|---------------------------------------|-----------------------------------------------------------------------|------------|
| `AIOpsDown`                           | `up{job="aiops-orchestrator"} == 0`                                   | critical   |
| `AIOpsApprovalBacklog`                | `aiops_approvals_pending > 10`                                        | warning    |
| `AIOpsApprovalBacklogCritical`        | `aiops_approvals_pending > 50`                                        | critical   |
| `AIOpsAnomalyDiagnoses`               | `rate(agent_router_aiops_diagnose_total{status=~"warning|critical"}[10m]) > 1` | warning |
| `AIOpsHighProviderFailureRate`        | `aiops_provider_failures_total / aiops_provider_calls_total > 0.2`    | warning    |
| `AIOpsLegacyEndpointsActive`          | `sum(aiops_legacy_endpoint_hits_total) > 0`                           | info       |
| `AIOpsBlockedActionsSpike`            | `increase(aiops_blocked_actions_total[5m]) > 5`                       | warning    |

---

## Notas de Segurança e Cardinalidade

1. **Todos os labels são enums normalizados** via `_normalize_label()` com allowlist explícita.
   Valores fora do allowlist caem no default sem propagar valor livre.

2. **Counters SQL (aiops_provider_calls_total, aiops_provider_failures_total,
   aiops_blocked_actions_total)** são derivados de `COUNT(*)` SQL e podem teoricamente
   decrementar se registros forem deletados. Usar como Gauge em dashboards.

3. **Sem exposição de LLM:** nenhum prompt, token, resposta ou contexto aparece em labels
   ou valores de métricas. O sistema de redação no audit log (docs/SECURITY.md) cobre
   a camada de logs; as métricas nunca incluem esses dados.

4. **Blue-green:** duas instâncias rodam simultaneamente (portas 8000 e 8001).
   O Prometheus deve scrape as duas com `job` labels distintos para diferenciar métricas
   entre `aiops-orchestrator` (produção) e `aiops-orchestrator-next` (canário).
