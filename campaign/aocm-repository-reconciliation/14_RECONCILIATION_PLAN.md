# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Plano de Reconciliação Pós-Adjudicação

**Campanha:** `aocm-repository-reconciliation`  
**Objetivo:** Roteiro ordenado e proporcional de transições a executar somente após aprovação humana

---

## 1. Princípios do Plano

1. **Zero Mutações Oportunistas:** Nenhuma refatoração, deleção de arquivos ou migração prematura é realizada durante a fase de censo.
2. **Separação Estrita de Slices:** Cada transição de conhecimento ou infraestrutura deve constituir uma PR focada com claims e contramodelos explícitos.
3. **Preservação de Guardrails:** Nenhum gate existente é flexibilizado para acomodar simplificações documentais.

---

## 2. Sequência Proposta de Transições (Slices)

### Slice 1 — Submissão da PR de Adoção Local do AOCM-MPACK
- **Branch:** `feat/adopt-aocm-mpack`
- **Conteúdo:** `.aocm/vendor/`, `.aocm/ADOPTION.md`, `.aocm/repository-profile.json`, `GEMINI.md`.
- **Efeito:** Integra formalmente o método local na branch `master` sem alterar código produtivo.

### Slice 2 — Arquivamento e Esclarecimento de Checkpoints Históricos
- **Objetivo:** Resolver o drift do `docs/engineering/CURRENT_CHECKPOINT.md` sem quebrar o header check do CAEM F0.
- **Ação:** Atualizar o cabeçalho para explicitar que o documento é um snapshot histórico de 2026-08-16, referenciando o `01_EXACT_SUBJECT.json` e a issue #46 para o estado vivo.

### Slice 3 — Delimitação da PR #327 e Fechamento de #329
- **Objetivo:** Adjudicar se o linter do ledger deve ser simplificado (restringindo-se a R1 e R3) ou se aguardará um novo mecanismo de mutação completa conforme a issue #329.
- **Ação:** Aplicar os princípios de *Successor Obligation Preservation* do MPACK.

### Slice 4 — Continuidade da Trilha Target Pack v2 (#203)
- **Objetivo:** Avançar a implementação dos comandos `validate` e `conformance` do target pack.
- **Ação:** Garantir paridade e conformance nos alvos `AgentEscala` e `InterLeitos`.
