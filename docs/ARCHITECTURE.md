# Orquestrador AIOps — Arquitetura

## Visão geral

O Orquestrador AIOps é um sistema seguro e modular de gerenciamento de tarefas com IA para ambientes homelab. Separa **pensar** (classificação, planejamento) de **executar** (execução) com verificações obrigatórias de políticas entre eles.

## Diagrama de arquitetura

```
┌─────────────────────────────────────────────────────────────────────┐
│                        WebAI (Open WebUI)                           │
│                    chat.ks-sm.net:9443                               │
│           CT 102 - Port 3001 -> container:8080                      │
└────────────────────────┬────────────────────────────────────────────┘
                         │ HTTP POST /v1/chat/ingest
                         │ (Bearer token auth)
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   AIOps Orchestrator (FastAPI)                       │
│                CT 102 - Port 8000 -> container:8000                  │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │  API      │  │  Orchestrator│  │ Policy Engine│  │  Task      │ │
│  │  Layer    │──│  Service     │──│              │  │  Service   │ │
│  │          │  │              │  │ - Denylist   │  │            │ │
│  │ /health  │  │ 1. Classify  │  │ - Risk eval  │  │ - CRUD     │ │
│  │ /ready   │  │ 2. Plan      │  │ - Approval   │  │ - Approve  │ │
│  │ /metrics │  │ 3. Validate  │  │   rules      │  │ - Audit    │ │
│  │ /v1/*    │  │ 4. Execute   │  │              │  │            │ │
│  └──────────┘  └──────┬───────┘  └──────────────┘  └─────┬──────┘ │
│                       │                                    │        │
│            ┌──────────┴──────────────┐            ┌───────┴──────┐ │
│            │   Provider Registry     │            │   SQLite DB  │ │
│            │                         │            │   (aiops.db) │ │
│            │  Route by role:         │            │              │ │
│            │  classify -> ollama     │            │  - tasks     │ │
│            │  plan     -> claude     │            │  - audit_log │ │
│            │  execute  -> local      │            │  - provider  │ │
│            │  summarize -> ollama    │            │    _calls    │ │
│            └──────────┬──────────────┘            │  - executions│ │
│                       │                           └──────────────┘ │
└───────────────────────┼────────────────────────────────────────────┘
                        │
         ┌──────────────┼──────────────────┐
         │              │                  │
         ▼              ▼                  ▼
┌─────────────┐ ┌─────────────┐  ┌──────────────┐
│   Ollama    │ │   Claude    │  │   OpenAI     │
│  (Local PC) │ │   (API)     │  │  Compatible  │
│             │ │             │  │  (Optional)  │
│ 192.168.3.87│ │ anthropic   │  │              │
│ :11434      │ │ .com        │  │              │
└─────────────┘ └─────────────┘  └──────────────┘

┌─────────────┐ ┌─────────────┐  ┌──────────────┐
│   Local     │ │   SSH       │  │   Docker     │
│  Executor   │ │  Executor   │  │   Adapter    │
│  (default)  │ │  (disabled) │  │  (read-heavy)│
└─────────────┘ └─────────────┘  └──────────────┘
```

## Fluxo de uma requisição

```
User message in WebAI chat
    │
    ├─ 1. POST /v1/chat/ingest (with Bearer token)
    │
    ├─ 2. CLASSIFY intent (via Ollama - fast, cheap)
    │      → {intent, category, risk_level, summary}
    │
    ├─ 3. EVALUATE intent against policy engine
    │      → blocked? → return immediately
    │      → query? → answer directly, no execution
    │      → action? → continue to planning
    │
    ├─ 4. PLAN execution (via Claude or Ollama fallback)
    │      → structured plan with steps, rollback, validation
    │
    ├─ 5. EVALUATE plan against policy engine
    │      → any blocked steps? → reject entire plan
    │      → risk level determines approval requirement
    │
    ├─ 6. APPROVAL gate
    │      → low risk + auto_approve → execute immediately
    │      → medium/high risk → await human approval
    │      → blocked → reject
    │
    ├─ 7. EXECUTE approved plan steps sequentially
    │      → each step re-validated before execution
    │      → backup files before modification
    │      → capture stdout/stderr/exit_code
    │      → stop on first failure
    │
    └─ 8. RECORD results in audit log
           → all decisions, commands, outputs logged
```

## Modelo de dados

| Tabela | Finalidade |
|-------|---------|
| `tasks` | Ciclo de vida da tarefa: mensagem, status, plano, resultado, aprovação |
| `audit_log` | Cada evento: criação, classificação, aprovação, execução |
| `provider_calls` | Rastreamento de chamadas LLM: provedor, tokens, latência |
| `executions` | Registros de execução: comando, saída, duração |

## Camadas de segurança

1. **Autenticação**: Token Bearer entre WebAI e orquestrador
2. **Classificação de intenção**: Avaliação de risco via LLM
3. **Motor de políticas**: Lista de bloqueio fixa + regras configuráveis
4. **Portão de aprovação**: Revisão humana para risco médio/alto
5. **Segurança de execução**: Timeout, limites de saída, mascaramento de segredos, backup antes de editar
6. **Trilha de auditoria**: Registro completo de cada decisão e ação

## Mapa de rede

| Serviço | Host | IP | Porta |
|---------|------|-----|------|
| Proxmox VE | pve | 192.168.3.50 | 8006 |
| Docker CT | CT 102 | 192.168.3.155 | - |
| AIOps Orchestrator | CT 102 (Docker) | 192.168.3.155 | 8000 |
| Open WebUI | CT 102 (Docker) | 192.168.3.155 | 3001 |
| NPM | CT 102 (Docker) | 192.168.3.155 | 80/443/81 |
| Monitor CT | CT 200 | 192.168.3.200 | - |
| Prometheus | CT 200 (Docker) | 192.168.3.200 | 9090 |
| Grafana | CT 200 (Docker) | 192.168.3.200 | 3000 |
| Ollama | PC | 192.168.3.87 | 11434 |
