# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Lacunas e Incertezas Abertas (Open Unknowns)

**Campanha:** `aocm-repository-reconciliation`  
**Escopo:** Mapeamento de ambiguidades e decisões dependentes de adjudicação humana

---

## 1. Inventário de Incertezas Materiais

A disciplina do AOCM-MPACK proíbe fabricar respostas para lacunas semânticas reais. Os itens abaixo são registrados formalmente como `UNKNOWN` aguardando decisão do mantenedor:

| ID | Área / Domínio | Incerteza / Pergunta Aberta | Impacto no Repositório | Bloqueio Imediato? |
|---|---|---|---|---|
| **UNK-01** | Release v1 | Qual é o número e data da próxima release da linha v1 (após a mesclagem da correção de planejamento de chunks #225)? | Não afeta o código do master; afeta a publicação para o AgentEscala. | Não |
| **UNK-02** | Target Pack v2 | Qual é o cronograma de implementação dos comandos pendentes (`validate`, `apply`, `rollback`) do Target Pack v2 (#203)? | O rollout mode do pack permanece congelado em `off`. | Não |
| **UNK-03** | Linter do Ledger (#327 / #329) | Qual critério de encerramento de mutação resolverá a completude do linter sem provocar outra rodada indefinida de patches? | A PR #327 permanece em DRAFT e não deve ser mesclada até resolução. | Sim (para a PR #327) |
| **UNK-04** | Checkpoint Stale no Contexto | Como desvincular com segurança `docs/engineering/CURRENT_CHECKPOINT.md` do `CLAUDE.md` sem quebrar o verificador de headers `verify-caem-f0-pin.py`? | Sessões recebem snapshot de 2026-08-16 como se fosse atual. | Não (informativo) |
| **UNK-05** | Adapters Legados | Qual é a janela formal de remoção para `app/adapters/` (que não integram o runner oficial de CT102)? | Manutenção de testes legados em `tests/test_legacy_adapter_quarantine.py`. | Não |
| **UNK-06** | Handoff V1 -> V2 | Em que momento o primeiro canário semântico real (`AgentEscala#759`) será executado para fechar formalmente a issue #200? | Issue #200 permanece formalmente aberta. | Não |

---

## 2. Princípio de Contenção

Nenhuma dessas incertezas autoriza a criação de regras temporárias inventadas. O repositório opera de forma determinística com base nas evidências existentes até que o mantenedor adjudique cada ponto.
