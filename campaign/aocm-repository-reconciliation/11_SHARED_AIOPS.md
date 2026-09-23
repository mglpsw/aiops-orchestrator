# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Superfícies Compartilhadas e Infraestrutura do AIOps

**Campanha:** `aocm-repository-reconciliation`  
**Escopo:** Infraestrutura compartilhada, runtime CT102, Router e fronteiras de integração

---

## 1. Separação de Superfícies Operacionais

O repositório `mglpsw/aiops-orchestrator` abriga duas superfícies operacionais estritamente segregadas:

```text
[CT102 / Runtime Produtivo]
  app/main.py, app/api/, app/services/, config/actions.yaml
  Objetivo: Orquestração de infraestrutura homelab
  Caminho: diagnose -> plan -> dry-run -> approval -> read-only run -> audit
  Fronteira: Isolado, sem shell livre, sem Docker exec, sem rede externa arbitrária

[CT104 / Toolrepo de Revisão de Código]
  app/agent_review/, schemas/agent-review/, evals/
  Objetivo: Revisão estática determinística de pull requests
  Caminho: intake -> diff extraction -> payload -> inference -> synthesis -> quality gate
  Fronteira: Executado offline em CI ou runners isolados
```

---

## 2. Mapa de Propriedade Semântica das Superfícies

| Superfície / Módulo | Caminho Físico | Semantic Owner | Classificação de Papel |
|---|---|---|---|
| Servidor FastAPI e Rotas HTTP | `app/main.py`, `app/api/` | AIOps-owned | Implementação de serviço de runtime homelab. |
| Catálogo de Ações Homelab | `config/actions.yaml`, `app/services/action_runner.py` | AIOps-owned | Contrato de ações permitidas e seguras em CT102. |
| Motor de Auditoria e Aprovações | `app/services/audit_service.py`, `app/services/approval_service.py` | AIOps-owned | Registro transacional imutável de operações. |
| Cliente do Agent Router | `app/agent_router/client.py` | Router-owned | Interface de transporte HTTPS para o gateway de inferência LLM (`agent-router-api`). |
| Roteamento Local de Provedores | `config/routes.yml` | AIOps-owned | Configuração local carregada pelo `ProviderRegistry` em `app/core/config.py` para selecionar adaptadores e executor local. |
| Sanitização de Segredos de Chat | `app/utils/secrets.py` | AIOps-owned | Utilitário de redação de segredos (`mask_secrets`, `truncate`) consumido pelo runtime e rotas de chat do AIOps. |
| Redação de Segredos do Review | `app/agent_review/redaction.py` | AgentReview-owned | Sanitizador determinístico do engine de review (v1, v2 e conformidade); não consumido pelo chat do AIOps. |
| Adapters Legados de Execução | `app/adapters/` (`docker.py`, `executor_ssh.py`, etc.) | legacy_supported | Código legado de execução consumido por `app/api/routes.py` via `Orchestrator` com `LocalExecutor` por padrão em CT102. |
| Contrato com Repositório Alvo | `docs/AGENTESCALA_TARGET_REPO_CONTRACT.md` | target-repository-owned | Especificação de como o AgentEscala consome o toolrepo por commit SHA. |
| Procedimento de Blue/Green Deploy | `deploy/docker-compose.bluegreen.yml`, `docs/bluegreen-deployment.md` | AIOps-owned | Runbook e infraestrutura de deploy de containers em CT102. |
| Scripts Utilitários de CI | `scripts/ci_validate.sh`, `scripts/test.sh` | AIOps-owned | Gates determinísticos locais executados em CI e pre-commit. |

---

## 3. Interfaces com Sistemas Externos

1. **Agent Router API (`mglpsw/agent-router-api`):**
   - Comunicação via HTTPS com autenticação por token (`AGENT_ROUTER_API_KEY`).
   - O AIOps é estritamente cliente consumidor de inferência; não hospeda modelos nem administra GPUs.
2. **Prometheus / Grafana (Homelab):**
   - O AIOps expõe métricas via rota `/metrics` (`app/api/metrics.py`) consumidas pelo scraper central do homelab.
   - O AIOps consome métricas de saúde via Prometheus query bounded para diagnósticos.
3. **GitHub Forge (CI / Bot):**
   - O workflow `agent-review.yml` e o script `scripts/github_agent_review.py` respondem a comentários de issues/PRs com validação estrita de atores autorizados (`AGENT_ALLOWED_USERS`).
