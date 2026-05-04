# Pacote de docs — GitHub Agent Review

Este pacote contém documentos para adicionar ao diretório `docs/` do repositório `mglpsw/aiops-orchestrator`.

## Arquivos

- `AI_CONTEXT_AIOPS_ORCHESTRATOR_REVIEWER.md`
  - contexto geral, problema observado e objetivo da melhoria.

- `AI_REVIEWER_SEVERITY_AND_EVIDENCE_RULES.md`
  - regras de severidade, evidência, anti-falso-positivo e template de comentário.

- `AI_AGENTESCALA_REVIEW_CONTEXT.md`
  - contexto específico para reviews de PRs do `mglpsw/AgentEscala`.

- `AI_GITHUB_AGENT_REVIEW_IMPROVEMENT_PROMPT.md`
  - prompt pronto para rodar no agent do repo `aiops-orchestrator`.

## Uso sugerido

Copiar para:

```bash
docs/AI_CONTEXT_AIOPS_ORCHESTRATOR_REVIEWER.md
docs/AI_REVIEWER_SEVERITY_AND_EVIDENCE_RULES.md
docs/AI_AGENTESCALA_REVIEW_CONTEXT.md
docs/AI_GITHUB_AGENT_REVIEW_IMPROVEMENT_PROMPT.md
```

Depois rodar o prompt de `AI_GITHUB_AGENT_REVIEW_IMPROVEMENT_PROMPT.md` no agent do repo.

## Objetivo da mudança

Fazer o reviewer automático:

- reduzir falsos positivos;
- não bloquear PR com base em diff truncado;
- separar bug confirmado de risco provável;
- carregar contexto do AgentEscala quando necessário;
- calibrar severidade P0/P1/P2/P3/INFO;
- não fingir testes executados;
- proteger contratos de escala médica sem atrapalhar PRs frontend-only.
