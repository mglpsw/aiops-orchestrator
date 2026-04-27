# AIOps Orchestrator — Arquitetura Canônica

## Visão geral

O AIOps Orchestrator é um sistema seguro e modular de orquestração orientado por diagnóstico. Separa
**observar** (coletar sinais, diagnosticar) de **planejar** (sugerir ações) de **executar** (ações
allowlisted, com aprovação humana obrigatória para ações de escrita).

**Fase atual: Diagnostic-only (v1).** Nenhum executor real está ativo no caminho produtivo.

---

## Componentes canônicos

```text
┌─────────────────────────────────────────────────────────────────────┐
│                        Orchestrator Core                            │
│                      CT 102 · porta 8000                            │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                      API Layer                               │   │
│  │  GET  /health   GET  /ready   GET  /metrics                  │   │
│  │  POST /v1/aiops/diagnose  (autenticado, dry-run only)        │   │
│  └──────────────────────────┬───────────────────────────────────┘   │
│                             │                                       │
│           ┌─────────────────┼─────────────────┐                    │
│           ▼                 ▼                 ▼                    │
│  ┌─────────────────┐ ┌────────────┐ ┌──────────────────┐          │
│  │ Diagnostic      │ │  Policy    │ │  Audit Log       │          │
│  │ Engine          │ │  Engine    │ │                  │          │
│  │                 │ │            │ │ - Cada decisão   │          │
│  │ - Coleta sinais │ │ - Denylist │ │ - Cada plano     │          │
│  │ - Calcula       │ │ - Risk eval│ │ - Cada simulação │          │
│  │   severity      │ │ - Approval │ │ - JSONL local    │          │
│  │ - Gera findings │ │   rules    │ │                  │          │
│  │ - dry_run only  │ │            │ │                  │          │
│  └─────────────────┘ └────────────┘ └──────────────────┘          │
│                                                                     │
│           ┌─────────────────────────────────────────┐              │
│           │              Action Planner              │              │
│           │                                         │              │
│           │  - Sugere ações do catálogo             │              │
│           │  - Nenhum comando livre                 │              │
│           │  - Saída: action_id + justificativa     │              │
│           │  - Aprovação humana obrigatória         │              │
│           └──────────┬──────────────────────────────┘              │
│                      │  (futuro — não ativo no v1)                 │
│          ┌───────────┴────────────┐                                │
│          ▼                        ▼                                │
│  ┌──────────────────┐  ┌──────────────────┐                       │
│  │ Local Agent      │  │ Remote Agent     │                       │
│  │ Bridge           │  │ Bridge           │                       │
│  │                  │  │                  │                       │
│  │ - Somente        │  │ - Somente        │                       │
│  │   allowlist      │  │   allowlist      │                       │
│  │ - Sem shell livre│  │ - Sem SSH livre  │                       │
│  │ - ISOLADO (v1)   │  │ - ISOLADO (v1)   │                       │
│  └──────────────────┘  └──────────────────┘                       │
└─────────────────────────────────────────────────────────────────────┘
```

### Componente: Orchestrator Core

Responsável por receber requisições, aplicar autenticação, roteamento e coordenar os demais componentes.

- **Implementado:** `app/main.py`, `app/api/auth.py`, `app/core/config.py`
- **Endpoints estáveis:** `/health`, `/ready`, `/metrics`, `/v1/aiops/diagnose`

### Componente: Diagnostic Engine

Coleta sinais internos (readiness, métricas, estado do serviço), calcula severidade e gera findings.
Sempre opera em `dry_run=true`. Nunca chama executores.

- **Implementado:** `app/agent_router/services/aiops_diagnostic.py`, `app/agent_router/schemas.py`
- **Camada adicional:** `app/agent_router/services/health_score.py`
- **Schemas:** `AIOpsDiagnoseRequest`, `AIOpsDiagnoseResponse`, `AIOpsSignal`, `AIOpsFinding`, `AIOpsRecommendedAction`
- **Health score:** valor determinístico de `0` a `100`, derivado dos findings/checks, sem LLM e sem execução
- **Finding enrichment:** `check`, `summary`, `impact`, `confidence`, `probable_cause`, `next_validation`, `recommended_action_ids`
- **Leitura de estado:** inclui sinais read-only existentes e checks degradados/safe-skipped quando uma fonte não está disponível

### Componente: Policy Engine

Lista de bloqueio fixa (hardcoded denylist) + regras configuráveis em YAML. Avalia risco e determina
se uma ação requer aprovação humana.

- **Implementado:** `app/policies/engine.py`, `config/policies.yml`
- **Denylist:** ver `docs/SECURITY.md`

### Componente: Action Planner

Sugere ações do catálogo (`config/actions.yaml`) com base nos findings do Diagnostic Engine.
Nenhum comando livre. Toda ação planejada requer aprovação humana antes de execução.

- **Implementado:** `app/services/action_planner.py`, `app/services/action_catalog.py`
- **Catálogo:** `config/actions.yaml` — validado no boot, cacheado em memória
- **Startup:** `init_catalog_on_startup()` carrega e valida o catálogo antes da primeira requisição
- **Readiness:** falha de catálogo degrada `/ready` para `not_ready` imediatamente no boot

### Componente: Local Agent Bridge

Executa ações allowlisted no host local após aprovação humana.

- **Status:** ISOLADO no v1 (não está no caminho produtivo)
- **Código:** `app/adapters/executor_local.py` — não usar diretamente
- **Ativação:** Futura, com allowlist estrutural obrigatória

### Componente: Remote Agent Bridge

Executa ações allowlisted em hosts remotos via SSH após aprovação humana.

- **Status:** ISOLADO no v1 (não está no caminho produtivo)
- **Código:** módulo de bridge remoto — não usar diretamente
- **Ativação:** Futura, com allowlist estrutural obrigatória

### Componente: Audit Log

Registra cada plano e simulação em formato estruturado, sem comandos ou segredos.

- **Implementado:** `app/agent_router/services/audit_log.py`
- **Storage:** JSONL local configurável (`logs/aiops_audit.jsonl` por padrão)
- **Endpoints associados:** `POST /v1/aiops/actions/plan`, `POST /v1/aiops/actions/dry-run`,
  `GET /v1/aiops/audit/recent`

---

## Fluxo de diagnóstico (v1 — caminho produtivo)

```text
POST /v1/aiops/diagnose
    │
    ├─ 1. Autenticação (Bearer token)
    │
    ├─ 2. Validação de schema (AIOpsDiagnoseRequest)
    │      dry_run obrigatório = true
    │
    ├─ 3. Diagnostic Engine
    │      → Coleta sinais: readiness, métricas, estado
    │      → Calcula findings, recommended_actions e health_score
    │      → Health score (0-100) deriva apenas de findings/checks
    │      → Gera findings enriquecidos e recommended_actions (texto, sem comando)
    │
    ├─ 4. Action Mapper (somente se há findings com problema)
    │      → app/agent_router/services/action_mapper.py
    │      → Mapeia signal names / check names → action_ids (tabela estática)
    │      → Sem LLM, sem texto livre, sem interpolação
    │      → action_ids gerais (git_status, git_log_recent) sempre adicionados
    │
    ├─ 5. Action Planner (fail-soft: catálogo ausente → action_plan = null)
    │      → Verifica cada action_id contra config/actions.yaml
    │      → Policy gate: mode=readonly, risk=low
    │      → Desconhecido ou policy-rejected → blocked_steps
    │      → Retorna ActionPlanResponse (dry_run=true, sem command)
    │      → Gera evento de auditoria action_plan_created
    │
    ├─ 6. Dry-run Simulation (POST /v1/aiops/actions/dry-run)
    │      → Reaproveita Action Planner + catálogo validado no startup
    │      → Normaliza would_run / blocked_steps / warnings
    │      → Não executa shell, processo externo, SSH, Docker, git ou systemctl
    │      → Retorna ActionDryRunResponse com execution="not_executed"
    │      → Gera evento de auditoria action_dry_run_created
    │
    ├─ 7. Audit Log
    │      → Registra eventos estruturados de plan/dry-run
    │      → GET /v1/aiops/audit/recent retorna eventos recentes
    │
    └─ 8. Retorna AIOpsDiagnoseResponse
           → dry_run: true (sempre)
           → action_plan: ActionPlanResponse | null
           → sem execução real
           → nenhum command exposto
```

## Fluxo do Action Planner (v1 — ativo, dry-run only)

```text
POST /v1/aiops/actions/plan
    │
    ├─ 1. Autenticação (Bearer token)
    │
    ├─ 2. Validação de schema (ActionPlanRequest)
    │      dry_run obrigatório = true
    │      action_ids = lista explícita de IDs do catálogo
    │
    ├─ 3. Action Catalog (app/services/action_catalog.py)
    │      → Carrega config/actions.yaml (fail-closed se ausente ou inválido)
    │      → Busca cada action_id no índice
    │      → Desconhecido? → blocked_steps
    │
    ├─ 4. Policy Gate (app/services/action_planner.py)
    │      → mode != readonly? → blocked_steps
    │      → risk != low?     → blocked_steps
    │      → Nenhum comando livre, nenhum shell, nenhuma interpolação
    │
    └─ 5. Retorna ActionPlanResponse
           → plan_id (UUID único por chamada)
           → steps: action_id, title, risk, mode, requires_approval, reason
           → blocked_steps: action_id + motivo
           → dry_run: true (sempre)
           → sem comando no output, sem execução real

GET /v1/aiops/actions/catalog
    │
    └─ Retorna CatalogResponse
           → action_id, description, mode, risk, timeout_seconds,
             requires_approval, tags
           → command NÃO é exposto na resposta
```

## Fluxo de Dry-Run (v1 — simulação segura)

```text
POST /v1/aiops/actions/dry-run
    │
    ├─ 1. Autenticação (Bearer token)
    │
    ├─ 2. Validação de schema (ActionDryRunRequest)
    │      dry_run obrigatório = true
    │      extra fields rejeitados (inclui command)
    │
    ├─ 3. Reuso do Action Catalog validado no startup
    │      → Fail-closed se o catálogo estiver inválido
    │
    ├─ 4. Reuso do Action Planner
    │      → Gera ActionPlanResponse sem comando
    │      → action_id desconhecido / policy-rejected → blocked_steps
    │
    ├─ 5. Dry-run simulation
    │      → Converte steps em would_run
    │      → execution="not_executed"
    │      → plan preservado para auditoria
    │
    └─ 6. Retorna ActionDryRunResponse
           → status: ok | partial | blocked
           → blocked_steps e warnings preservados
           → nenhum command exposto
```

## Fluxo de execução (futuro — não ativo no v1)

```text
[ActionDryRunResponse ou ActionPlanResponse com status=ready]
    │
    ├─ Approval Gate (obrigatório)
    │      → Human review explícita para cada step
    │      → Sem auto-aprovação
    │
    └─ Agent Bridge (Local ou Remote)
           → Executa somente action_ids allowlisted
           → Timeout, mascaramento de segredos, log de saída
           → Não ativo até v2+
```

---

## Portas e runtimes

| Runtime           | Porta | Papel                                               |
| ----------------- | ----- | --------------------------------------------------- |
| Produção estável  | 8000  | Caminho produtivo — nunca alterar sem aprovação     |
| Next / observe    | 8001  | Blue/green para validação — sem promoção automática |

**Regra:** 8001 nunca é promovido automaticamente. Promoção é sempre manual e documentada.

---

## Modelo de dados

| Tabela            | Finalidade                                                   |
| ----------------- | ------------------------------------------------------------ |
| `tasks`           | Ciclo de vida: mensagem, status, plano, resultado, aprovação |
| `audit_log`       | Eventos: criação, diagnóstico, aprovação, execução           |
| `provider_calls`  | Chamadas LLM: provedor, tokens, latência                     |
| `executions`      | Execuções: action_id, saída, duração                         |

---

## Mapa de rede

| Serviço             | CT/Host          | IP             | Porta     |
| ------------------- | ---------------- | -------------- | --------- |
| Proxmox VE          | pve              | 192.168.3.50   | 8006      |
| AIOps Orchestrator  | CT 102 (Docker)  | 192.168.3.155  | 8000      |
| AIOps Next/Observe  | CT 102 (Docker)  | 192.168.3.155  | 8001      |
| Open WebUI          | CT 102 (Docker)  | 192.168.3.155  | 3001      |
| NPM                 | CT 102 (Docker)  | 192.168.3.155  | 80/443/81 |
| Prometheus          | CT 200 (Docker)  | 192.168.3.200  | 9090      |
| Grafana             | CT 200 (Docker)  | 192.168.3.200  | 3000      |
| Ollama              | PC local         | 192.168.3.87   | 11434     |

---

## Componentes isolados (não usar no v1)

| Arquivo                            | Motivo do isolamento                                |
| ---------------------------------- | --------------------------------------------------- |
| `app/adapters/executor_local.py`   | Shell real via helper interno de processos          |
| módulo de bridge remoto           | SSH remoto via shell — risco muito alto             |
| `app/adapters/docker.py`           | Docker exec via shell                               |
| `app/adapters/codex.py`            | Automação de código/infra — fora do escopo v1       |
| `app/services/orchestrator.py`     | Mistura classificação, planejamento e execução real |

Esses arquivos permanecem no repo para referência e evolução futura, mas não fazem parte do
caminho produtivo do Diagnostic Engine v1.
