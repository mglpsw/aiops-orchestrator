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
│  │ - Coleta sinais │ │ - Denylist │ │ - Cada diagnóst. │          │
│  │ - Calcula       │ │ - Risk eval│ │ - Cada approval  │          │
│  │   severity      │ │ - Approval │ │ - SQLite aiops.db│          │
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
- **Schemas:** `AIOpsDiagnoseRequest`, `AIOpsDiagnoseResponse`, `AIOpsSignal`, `AIOpsFinding`, `AIOpsRecommendedAction`

### Componente: Policy Engine

Lista de bloqueio fixa (hardcoded denylist) + regras configuráveis em YAML. Avalia risco e determina
se uma ação requer aprovação humana.

- **Implementado:** `app/policies/engine.py`, `config/policies.yml`
- **Denylist:** ver `docs/SECURITY.md`

### Componente: Action Planner

Sugere ações do catálogo (`config/actions.yaml`) com base nos findings do Diagnostic Engine.
Nenhum comando livre. Toda ação planejada requer aprovação humana antes de execução.

- **Status:** Parcialmente implementado via `app/services/orchestrator.py` (legado)
- **Evolução:** Migrar para catálogo estruturado + approval gate explícito
- **Catálogo:** `config/actions.yaml` (somente read-only no v1)

### Componente: Local Agent Bridge

Executa ações allowlisted no host local após aprovação humana.

- **Status:** ISOLADO no v1 (não está no caminho produtivo)
- **Código:** `app/adapters/executor_local.py` — não usar diretamente
- **Ativação:** Futura, com allowlist estrutural obrigatória

### Componente: Remote Agent Bridge

Executa ações allowlisted em hosts remotos via SSH após aprovação humana.

- **Status:** ISOLADO no v1 (não está no caminho produtivo)
- **Código:** `app/adapters/executor_ssh.py` — não usar diretamente
- **Ativação:** Futura, com allowlist estrutural obrigatória

### Componente: Audit Log

Registra cada evento: criação de tarefa, diagnóstico, decisão de política, aprovação, execução.

- **Implementado:** `app/models/database.py`, `app/services/task_service.py`
- **Storage:** SQLite em volume persistente (`aiops-data:/app/data/aiops.db`)

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
    │      → Calcula severity: ok | warning | critical | unknown
    │      → Gera findings e recommended_actions (texto, sem comando)
    │
    ├─ 4. Audit Log
    │      → Registra diagnóstico e resultado
    │
    └─ 5. Retorna AIOpsDiagnoseResponse
           → dry_run: true (sempre)
           → sem execução real
```

## Fluxo de ação planejada (futuro — não ativo no v1)

```text
[Findings do Diagnostic Engine]
    │
    ├─ Action Planner
    │      → Seleciona action_id do catálogo (config/actions.yaml)
    │      → Nenhum comando livre permitido
    │
    ├─ Policy Engine
    │      → Avalia risco da ação
    │      → Bloqueada? → rejeitar
    │
    ├─ Approval Gate (obrigatório)
    │      → Human review sempre para ações do catálogo
    │      → Sem auto-aprovação no v1+
    │
    └─ Agent Bridge (Local ou Remote)
           → Executa somente ações allowlisted
           → Timeout, mascaramento de segredos, log de saída
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
| `app/adapters/executor_local.py`   | Shell real via `asyncio.create_subprocess_shell`    |
| `app/adapters/executor_ssh.py`     | SSH remoto via shell — risco muito alto             |
| `app/adapters/docker.py`           | Docker exec via shell                               |
| `app/adapters/codex.py`            | Automação de código/infra — fora do escopo v1       |
| `app/services/orchestrator.py`     | Mistura classificação, planejamento e execução real |

Esses arquivos permanecem no repo para referência e evolução futura, mas não fazem parte do
caminho produtivo do Diagnostic Engine v1.
