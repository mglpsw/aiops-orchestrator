# Homelab Handoff — aiops-orchestrator

> **Data:** 2026-04-27
> **Destino:** Prometheus CT 200 / Grafana CT 200
> **Fonte de verdade de métricas:** `docs/metrics-contract.md`

---

## Localização Canônica

| Item              | Valor                                      |
|-------------------|--------------------------------------------|
| CT               | CT 102 (hostname: `docker`, IP: `192.168.3.155`) |
| Path             | `/opt/aiops-orchestrator`                  |
| Container prod   | `aiops-orchestrator` — porta **8000**      |
| Container canário| `aiops-orchestrator-next` — porta **8001** |
| Protocolo        | HTTP                                       |
| Base URL prod    | `http://192.168.3.155:8000`                |
| Base URL canário | `http://192.168.3.155:8001`                |

> **Blue-green:** dois containers rodam simultaneamente. O Prometheus deve scrape
> ambas as instâncias com `job` distintos.

---

## Endpoints

| Endpoint                               | Método | Acesso    | Descrição                                        |
|----------------------------------------|--------|-----------|--------------------------------------------------|
| `/health` e `/healthz`                 | GET    | público   | Liveness — retorna `{"status":"healthy",...}`    |
| `/ready` e `/readyz`                   | GET    | público   | Readiness — verifica DB, providers, action_catalog |
| `/metrics`                             | GET    | público   | Métricas Prometheus em text/plain                |
| `/docs`                                | GET    | debug only| OpenAPI UI (desabilitado em produção)            |
| `/v1/aiops/diagnose`                   | POST   | protegido | Diagnóstico do sistema                           |
| `/v1/aiops/actions/catalog`            | GET    | protegido | Catálogo de ações permitidas                     |
| `/v1/aiops/actions/plan`               | POST   | protegido | Planejar ações                                   |
| `/v1/aiops/actions/dry-run`            | POST   | protegido | Simular ação sem executar                        |
| `/v1/aiops/actions/approvals`          | POST/GET | protegido | Criar / listar aprovações                      |
| `/v1/aiops/actions/approvals/{id}/approve` | POST | protegido | Aprovar ação                                 |
| `/v1/aiops/actions/approvals/{id}/reject`  | POST | protegido | Rejeitar ação                                |
| `/v1/aiops/actions/run`                | POST   | protegido | Executar ação aprovada (read-only)               |
| `/v1/aiops/runs/recent`                | GET    | protegido | Histórico de runs                                |
| `/v1/aiops/audit/recent`               | GET    | protegido | Log de auditoria recente                         |

**Endpoints protegidos:** requerem header `Authorization: Bearer <AIOPS_API_TOKEN>`.

**Endpoints legados (deprecated, emitem header `Deprecation: true`):**
`/v1/chat`, `/v1/chat/ingest`, `/v1/tasks`, `/v1/tasks/{id}`,
`/v1/approvals`, `/v1/approvals/{id}`, `/v1/providers/status`

---

## Scrape Config Recomendado (Prometheus CT 200)

Adicionar ao `monitoring/prometheus/prometheus.yml` no homelab:

```yaml
  # aiops-orchestrator — produção (porta 8000)
  - job_name: "aiops-orchestrator"
    static_configs:
      - targets: ["192.168.3.155:8000"]
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    relabel_configs:
      - target_label: service
        replacement: aiops-orchestrator
      - target_label: instance
        replacement: ct102:8000

  # aiops-orchestrator-next — canário blue-green (porta 8001)
  - job_name: "aiops-orchestrator-next"
    static_configs:
      - targets: ["192.168.3.155:8001"]
    metrics_path: /metrics
    scrape_interval: 30s
    scrape_timeout: 10s
    relabel_configs:
      - target_label: service
        replacement: aiops-orchestrator-next
      - target_label: instance
        replacement: ct102:8001
```

---

## Métricas aiops_* Oficiais

| Métrica                                    | Tipo    | Labels               | Fonte            |
|--------------------------------------------|---------|----------------------|------------------|
| `aiops_tasks_total`                        | Gauge   | —                    | SQL COUNT tasks  |
| `aiops_tasks_by_status`                    | Gauge   | `status`             | SQL GROUP BY     |
| `aiops_provider_calls_total`¹              | Counter | —                    | SQL COUNT calls  |
| `aiops_provider_failures_total`¹           | Counter | —                    | SQL COUNT fails  |
| `aiops_approvals_pending`                  | Gauge   | —                    | SQL COUNT        |
| `aiops_blocked_actions_total`¹             | Counter | —                    | SQL COUNT        |
| `agent_router_aiops_diagnose_total`        | Counter | `status`, `severity` | in-memory        |
| `agent_router_aiops_diagnose_latency_seconds` | Counter | `status`, `severity` | in-memory    |
| `agent_router_aiops_findings_total`        | Counter | `severity`           | in-memory        |
| `aiops_legacy_endpoint_hits_total`         | Counter | `endpoint`           | in-memory        |

¹ Derivados de SQL COUNT — podem decrementar. Usar como Gauge em dashboards.

Referência completa: `docs/metrics-contract.md`.

---

## Alertas Recomendados

Adicionar ao `monitoring/prometheus-rules-aiops.yml` no homelab:

```yaml
  - alert: AIOpsDown
    expr: up{job="aiops-orchestrator"} == 0
    for: 1m
    labels:
      severity: critical
    annotations:
      summary: "aiops-orchestrator indisponível"
      description: "CT 102:8000 — aiops-orchestrator não responde ao scrape há 1 min."

  - alert: AIOpsApprovalBacklog
    expr: aiops_approvals_pending > 10
    for: 10m
    labels:
      severity: warning
    annotations:
      summary: "Backlog de aprovações aiops acima de 10"

  - alert: AIOpsApprovalBacklogCritical
    expr: aiops_approvals_pending > 50
    for: 5m
    labels:
      severity: critical
    annotations:
      summary: "Backlog crítico de aprovações aiops"

  - alert: AIOpsAnomalyDiagnoses
    expr: rate(agent_router_aiops_diagnose_total{status=~"warning|critical"}[10m]) > 1
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Diagnósticos com anomalia acima de 1/s"

  - alert: AIOpsHighProviderFailureRate
    expr: >
      aiops_provider_failures_total / aiops_provider_calls_total > 0.2
    for: 5m
    labels:
      severity: warning
    annotations:
      summary: "Taxa de falha de provider LLM acima de 20%"

  - alert: AIOpsLegacyEndpointsActive
    expr: sum(aiops_legacy_endpoint_hits_total) > 0
    labels:
      severity: info
    annotations:
      summary: "Endpoints legados do aiops ainda sendo usados"
```

---

## Dashboard Grafana Planejado

| Item           | Valor                                                     |
|----------------|-----------------------------------------------------------|
| UID sugerido   | `aiops-orchestrator`                                      |
| Arquivo        | `monitoring/grafana/dashboards/iacenter/aiops-orchestrator.json` |
| Variáveis      | `$job` (aiops-orchestrator \| aiops-orchestrator-next)    |

Painéis planejados:
1. Status de saúde (`up{job="aiops-orchestrator"}`)
2. Tarefas por status (`aiops_tasks_by_status`)
3. Fila de aprovações (`aiops_approvals_pending`)
4. Diagnósticos por resultado (`agent_router_aiops_diagnose_total`)
5. Latência média de diagnóstico
6. Findings por severidade (`agent_router_aiops_findings_total`)
7. Taxa falha/sucesso de providers
8. Endpoints legados em uso (`aiops_legacy_endpoint_hits_total`)

> Dashboard não criado nesta fase. Criar após validar scrape com dados reais.

---

## Validações curl

```bash
# Liveness (prod)
curl -sf http://192.168.3.155:8000/health | python3 -m json.tool

# Readiness (prod)
curl -sf http://192.168.3.155:8000/ready | python3 -m json.tool

# Liveness (canário)
curl -sf http://192.168.3.155:8001/health | python3 -m json.tool

# Métricas — listar todas as aiops_* e agent_router_aiops_*
curl -sf http://192.168.3.155:8000/metrics | grep -E "^(aiops_|agent_router_aiops_|# )"

# Confirmar ausência de agent_router_* puro (pertence ao agent-router-api :8010)
curl -sf http://192.168.3.155:8000/metrics | grep "^agent_router_[^a]" || echo "OK — namespace isolado"

# Verificar scrape pelo Prometheus (após adicionar o job)
curl -sf http://prometheus:9090/api/v1/targets | python3 -m json.tool | grep -A5 "aiops-orchestrator"
```

---

## Troubleshooting

| Sintoma                                   | Ação                                                                  |
|-------------------------------------------|-----------------------------------------------------------------------|
| `up{job="aiops-orchestrator"} == 0`       | `pct exec 102 -- docker ps \| grep aiops`; checar porta 8000         |
| `/ready` retorna `ready: false`           | Checar `checks` no JSON: DB, providers ou action_catalog falhando    |
| Nenhuma métrica `aiops_*`                 | `/metrics` acessível? Container healthy? DB inicializado?            |
| `aiops_approvals_pending` crescendo       | Revisar fila via `GET /v1/aiops/actions/approvals`                   |
| `aiops_provider_failures_total` alto      | Verificar `GET /v1/providers/status` para ver qual provider falhou   |
| Diagnósticos sem métricas                 | Métricas `agent_router_aiops_*` são in-memory — reiniciar zera        |
| Métricas `agent_router_*` puro no :8000  | Não deveria acontecer — bug de namespace se ocorrer                  |

---

## Fronteira com agent-router-api e homelab

```
┌─────────────────────────────────────────────────────────────┐
│                         CT 102                              │
│                                                             │
│  agent-router-api     :8010  →  agent_router_* metrics      │
│  aiops-orchestrator   :8000  →  aiops_* + agent_router_aiops_* metrics │
│  aiops-orchestrator-next :8001  → (blue-green canário)      │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │ Prometheus scrape
┌──────────────────────▼──────────────────────────────────────┐
│                         CT 200                              │
│                                                             │
│  Prometheus  →  jobs: agent-router-api, aiops-orchestrator  │
│  Grafana     →  dashboards separados por serviço            │
│  Alertas     →  AgentRouter* em prometheus-rules-aiops.yml  │
│                 AIOps* em prometheus-rules-aiops.yml         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Regras de convivência:**
- `job="agent-router-api"` → scrape `:8010` → métricas `agent_router_*`
- `job="aiops-orchestrator"` → scrape `:8000` → métricas `aiops_*` e `agent_router_aiops_*`
- Os dois jobs nunca devem se misturar em dashboards ou alertas
- Label `service` via `relabel_configs` identifica o serviço em cada query

---

## Próximos Passos

1. Adicionar scrape jobs no homelab: `monitoring/prometheus/prometheus.yml`
2. Validar scrape: `curl -sf http://192.168.3.155:8000/metrics | grep ^aiops_`
3. Adicionar alertas: `monitoring/prometheus-rules-aiops.yml`
4. Criar dashboard após dados reais disponíveis
5. Avaliar converter counters SQL para Gauges explícitos no código
