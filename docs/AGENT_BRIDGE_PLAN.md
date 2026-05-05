# GitHub Agent Bridge - Supervised Actions Plan

## Contexto

Este documento descreve o design futuro para GitHub Agent Bridge supervisionado, preparando a infraestrutura sem implementar execução mutável agora.

O GitHub Agent Review atual (após PR #26 e issue #27) fornece:
- Review determinístico + LLM com anti-falso-positivo
- Context bundle v2 com metadados completos
- Evidence-based findings com rastreabilidade
- Telemetria segura sem exposição de secrets
- Output v3 estruturado

A próxima fase permitirá ações supervisionadas **sem deploy automático, merge automático ou execução arbitrária**.

## Objetivo

Preparar o design para ações supervisionadas que o Agent Review pode **sugerir** mas **nunca executar automaticamente**.

## Princípios de Segurança

### O que o Agent Bridge NÃO deve fazer (permanente):

1. **Nunca** fazer merge automático
2. **Nunca** fazer deploy para produção
3. **Nunca** executar comandos arbitrários (shell livre)
4. **Nunca** usar SSH
5. **Nunca** executar `docker exec`
6. **Nunca** fazer `git push` direto
7. **Nunca** tocar CT102 (produção)
8. **Nunca** reiniciar serviços em produção
9. **Nunca** alterar configuração do AgentEscala
10. **Nunca** executar sem aprovação humana

### O que o Agent Bridge PODE fazer (supervisionado):

1. **Comentar PR** com diagnóstico detalhado
2. **Anexar run history** quando disponível
3. **Resumir CI** com links para logs
4. **Abrir issue de follow-up** para tracking
5. **Acionar workflow dry-run** aprovado previamente
6. **Adicionar labels** em PRs/issues
7. **Request reviewers** específicos
8. **Atualizar status checks** (commit status API)

Todas essas ações exigem:
- Approval workflow configurado
- Audit log completo
- Fail-closed em caso de erro
- Rate limiting
- Redaction de secrets

## Design Proposto

### 1. Action Registry

```python
@dataclass(frozen=True)
class BridgeAction:
    """Ação supervisionada que o Agent Bridge pode executar."""
    action_id: str  # "comment_pr", "open_issue", "add_label"
    requires_approval: bool  # True para todas as ações inicialmente
    approval_threshold: int  # Quantas aprovações necessárias (min 1)
    allowed_repos: tuple[str, ...]  # Lista de repos permitidos
    rate_limit_per_hour: int  # Max execuções por hora
    audit_required: bool = True  # Sempre True
    dry_run_available: bool = False  # Se tem modo dry-run
```

### 2. Approval Workflow

Antes de executar qualquer ação:

1. Agent Review identifica ação sugerida
2. Cria **request de approval** com:
   - Ação proposta (ex: "add_label:needs-testing")
   - Justificativa (baseada em findings)
   - Preview do resultado
   - Risk assessment
3. Espera aprovação de OWNER/MEMBER
4. Somente após aprovação, executa
5. Registra audit trail completo

### 3. Ações Seguras (Fase 1)

#### 3.1 Comentar PR com diagnóstico

```yaml
action: comment_pr
approval: required
input:
  - review_summary: markdown
  - findings: list[Finding]
  - bundle_metadata: ReviewBundle
guardrails:
  - redact_secrets: true
  - max_comment_chars: 5000
  - no_@mentions_without_approval: true
```

#### 3.2 Resumir CI

```yaml
action: summarize_ci
approval: required
input:
  - pr_number: int
  - failed_checks: list[str]
output:
  - ci_summary: markdown com links
guardrails:
  - only_read_access: true
  - no_trigger_workflows: true
```

#### 3.3 Abrir issue de follow-up

```yaml
action: open_followup_issue
approval: required
input:
  - title: str
  - body: markdown
  - labels: list[str]
  - assignees: list[str]
guardrails:
  - max_1_issue_per_pr: true
  - only_in_same_repo: true
  - no_secrets_in_body: true
```

### 4. Ações Proibidas (Sempre)

Estas ações **nunca** serão implementadas:

- `merge_pr` → sempre bloqueado
- `deploy_to_production` → sempre bloqueado
- `run_shell_command` → sempre bloqueado
- `ssh_execute` → sempre bloqueado
- `docker_exec` → sempre bloqueado
- `git_push` → sempre bloqueado
- `restart_service` → sempre bloqueado
- `modify_agentescala` → sempre bloqueado

## Implementação Futura

### Fase 1: Comentários e Resumos (somente leitura + escrita segura)

1. Implementar `BridgeAction` registry
2. Implementar approval workflow
3. Adicionar ações:
   - `comment_pr`
   - `summarize_ci`
   - `open_followup_issue`
4. Testes completos com dry-run
5. Audit log estruturado

### Fase 2: Labels e Status (metadata segura)

1. Adicionar ações:
   - `add_labels`
   - `request_reviewers`
   - `update_commit_status`
2. Implementar rate limiting
3. Implementar circuit breaker
4. Testes de edge cases

### Fase 3: Dry-run de Workflows (sem execução real)

1. Validar workflow YAML
2. Simular execução (análise estática)
3. Reportar resultado simulado
4. **Nunca executar workflow automaticamente**

## Telemetria

Registrar métricas seguras:

```python
bridge_action_requested: action_id, repo, pr_number, timestamp
bridge_action_approved: action_id, approver, timestamp
bridge_action_executed: action_id, success/failure, duration_ms
bridge_action_denied: action_id, reason
bridge_action_rate_limited: action_id, hour
```

**Nunca registrar:**
- Tokens
- Secrets
- API keys
- Headers sensíveis
- Corpo completo de comentários com secrets

## Compatibilidade com Context Bundle v2

O Agent Bridge usará os metadados do ReviewBundle v2:

- `diff_available`, `diff_truncated` → decidir se ação é segura
- `checks_observed`, `failed_checks` → input para `summarize_ci`
- `files_by_area` → sugerir reviewers especializados
- `analyzed_commit` → link para commit no follow-up issue
- `validation_local` → incluir no diagnóstico

## Integração com Evidence-based Findings

Findings com `evidence_state` e `evidence_source` alimentam decisão de ações:

- `evidence_state=confirmed` + `evidence_source=diff` → alta confiança para `open_followup_issue`
- `evidence_state=unconfirmed_truncated` → baixa confiança, apenas `comment_pr` com disclaimer
- `downgrade_reason` presente → mencionar no follow-up issue
- `critical_evidence_preserved=true` → incluir no resumo CI

## Configuração

```yaml
# .github/aiops-bridge.yml
bridge:
  enabled: false  # Disabled por padrão
  allowed_actions:
    - comment_pr
    - summarize_ci
    - open_followup_issue
  approval:
    required: true
    threshold: 1
    allowed_approvers:
      - OWNER
      - MEMBER
  rate_limits:
    comment_pr: 10  # por hora
    open_followup_issue: 3  # por hora
  audit:
    enabled: true
    log_level: INFO
```

## Critérios de Aceite (Fase 1 Futura)

- [ ] `BridgeAction` registry implementado
- [ ] Approval workflow funcional
- [ ] `comment_pr` implementado e testado
- [ ] `summarize_ci` implementado e testado
- [ ] `open_followup_issue` implementado e testado
- [ ] Audit log estruturado e testável
- [ ] Rate limiting funcional
- [ ] Circuit breaker em caso de falha
- [ ] Redaction de secrets aplicada
- [ ] Testes cobrem todos os cenários
- [ ] Documentação completa
- [ ] Merge automático continua bloqueado
- [ ] Deploy automático continua bloqueado
- [ ] Execução mutável continua bloqueada

## Riscos Residuais

Mesmo com aprovação, ações supervisionadas têm riscos:

1. **Spam de comentários** → rate limiting + circuit breaker
2. **Issues duplicados** → validar existência antes de criar
3. **Aprovação acidental** → exigir confirmação explícita
4. **Escalação de privilégios** → allowlist de repos e ações
5. **Exposição de secrets em comentários** → redaction obrigatória

## Próximos Passos

Esta é apenas documentação de design. Implementação real requer:

1. RFC/discussão com time
2. Aprovação de segurança
3. Implementação incremental (Fase 1 → Fase 2 → Fase 3)
4. Testes extensivos em ambiente staging (CT104)
5. Rollout gradual com feature flag

**NÃO implementar antes de aprovação formal.**

## Referências

- Issue #27: phase(review): improve AIOps Agent Review context, telemetry and bridge readiness
- PR #26: fix(review): prevent false positives from speculative LLM findings
- docs/AI_REVIEWER_SEVERITY_AND_EVIDENCE_RULES.md
- docs/GITHUB_AGENT.md
