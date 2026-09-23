# Registro de Adoção Metodológica — AOCM-MPACK

Data de registro: 2026-09-23
Repositório-alvo: `mglpsw/aiops-orchestrator`
Pacote adotado: `AOCM-MPACK 0.1.0-preview.1`
Status do ciclo de vida: `ADOPTED_BY_MAINTAINER`

---

## 1. Decisão do Mantenedor

> O mantenedor adota o AOCM-MPACK 0.1.0-preview.1 como método local de planejamento, construção, revisão, reparo, qualificação e encerramento de mudanças do repositório mglpsw/aiops-orchestrator. Sua força local deriva desta decisão humana, não de autodeclaração do pacote. Aplicam-se o núcleo e os perfis pertinentes, de maneira proporcional. A adoção não transfere autoridade de domínio, não modifica contratos de runtime, não ativa conformidade CAEM e não autoriza operações protegidas.

Referência do grant humano: Concedido pelo mantenedor Miguel (`mglpsw`) em sessão de engenharia de 2026-09-23.

---

## 2. Precedência por Domínio e Governança

A precedência de autoridades neste repositório é estritamente demarcada por domínio, evitando supersessões implícitas ou diluição de contratos existentes:

1. **Comportamento, Schemas, Segurança e Engine do Produto**:
   - Os contratos canônicos e owners de `mglpsw/aiops-orchestrator` e `AgentReview` governam com exclusividade a execução em produção, esquemas de produto, intake, chunking, sanitização, parsers, síntese, quality/readiness gates, publisher, Router, runners e resultados publicáveis (`docs/engineering/PROJECT_OVERLAY.md`, issue #46 como autoridade canônica de roadmap; `docs/AGENT_REVIEW_V2_ROADMAP.md` permanece arquivado como snapshot histórico de 2026-07-23).
2. **Procedimento Local de Engenharia**:
   - O `AOCM-MPACK 0.1.0-preview.1` governa o método operacional local (planejamento, derivação de obrigações, testes causais, contramodelos, ciclo de PR e isolamento de claims). O método auxilia o processo de engenharia e não substitui a interpretação competente do domínio.
3. **Norma CAEM (Upstream)**:
   - A autoridade normativa pertence exclusivamente a `mglpsw/caem`. O pin local do AIOps (`config/caem/caem-3.0-f0.pin.json`) permanece upstream e inalterado por esta adoção.
4. **Governança de Mudanças Estruturais e Limiares STOP/REDESIGN**:
   - O owner exclusivo dos critérios, limiares e procedimentos de `STOP/REDESIGN` é e permanece sendo `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`. O MPACK não inventa conjuntos concorrentes de limiares.
5. **Infraestrutura CI e Workflows**:
   - As políticas de menor privilégio de tokens e segurança de workflows permanecem governadas por `.github/AGENTS.md` e `.github/workflows/`.
6. **Evidências, Checkpoints, Corpus e Modelos**:
   - Materiais observacionais e históricos preservam seus contextos e papéis específicos; sua presença local não confere autoridade normativa.
7. **Tratamento de Conflitos**:
   - Conflitos materiais de autoridade ou integridade devem ser nomeados explicitamente e encaminhados ao respectivo owner; não há equivalências tácitas, dispensas nem supersessões implícitas.

---

## 3. Reconciliação com Instruções e Guardrails Existentes

- **O que complementa**:
  - Disciplina de isolamento de claims e non-claims;
  - Testes causais com controles positivos e contramodelos focais;
  - Matriz de obrigações em mudanças com múltiplos consumidores materiais;
  - Distinção explícita entre integridade de recibo e julgamento semântico (`ReceiptIntegrityValidation != ReceiptSemanticAdjudication`);
  - Separação de planos no ciclo de PR (declaração de boundary, reparo lógico isolado, fechamento governado).
- **O que apenas referencia**:
  - `AGENTS.md` e `CLAUDE.md` como projeções operacionais locais consolidadas;
  - `docs/engineering/PROJECT_OVERLAY.md` para fronteiras de confiança e readiness do orquestrador;
  - `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` para checklist de pré-voo e gates de redesenho.
- **O que permanece fora de escopo**:
  - Execução de APCP/Q1 upstream;
  - Transformação do AgentReview em engine de qualificação do MPACK para outros repositórios;
  - Alterações no homelab, bancos de dados, providers reais, regras de auto-merge ou auto-deploy;
  - Modificação ou migração de clientes/consumidores externos.

---

## 4. Separação de Estados do Registro

- **Decisão Humana**: Concedida por Miguel (`mglpsw`) em sessão de engenharia de 2026-09-23.
- **Aplicação Local**: Executada no worktree local mantendo o pacote íntegro em `.aocm/vendor/`.
- **Integração na Branch Principal**: Integrada em `master` via PR #344 (commit `9dd69b964cbeef4615307758f90a3f83116b25f9`). Estado vivo subsequente deve ser revalidado no forge / git.
- **Qualificação Observada**: 57/57 testes do pacote aprovados, manifest `c16f9abe...` verificado, `verify-caem-f0-pin.py` verificado e gates de CI aprovados.
- **Revisão Independente**: Revisão Codex concluída (PR #344); observações locais reconciliadas.
- **Ações Protegidas**: Push, merge, PR remota, release, deploy e ativação de checks bloqueantes permanecem governados pela matriz de autoridade local.
