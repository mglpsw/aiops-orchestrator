# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Linhagem Metodológica Pré-AOCM e Contribuições Locais

**Campanha:** `aocm-repository-reconciliation`  
**Escopo:** Mapeamento histórico dos conceitos de governança e métodos nascidos no AIOps

---

## 1. Origem Local dos Conceitos e Relação com o AOCM-MPACK

O AOCM-MPACK não surgiu no vácuo; ele absorveu e formalizou princípios de engenharia desenvolvidos empiricamente ao longo das campanhas do AIOps e de repositórios correlatos.

A tabela abaixo classifica os principais conceitos metodológicos observados no repositório:

| Conceito Local | Documento de Origem | Classificação | Relação e Linhagem com o AOCM-MPACK |
|---|---|---|---|
| **Authority-First Convergence Review** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` | `pre-AOCM / still-local-authority` | Estabeleceu que autoridade precede a relação e o código. Incorporado como núcleo de MP-K01. |
| **Mandatory STOP / REDESIGN Condition** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` | `still-local-authority` | Critérios formais de parada de arquitetura. O owner exclusivo deste mecanismo permanece local. |
| **Exact-Subject Evidence** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`, PR #200-G1 | `pre-AOCM / AOCM-compatible` | Prova pertence ao objeto consumido por commit SHA de 40 caracteres, não a branch ou tag. Base direta de MP-K02. |
| **Circularity Rule & Causal Mutation Testing** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`, PR #322, PR #327 | `pre-AOCM / AOCM-compatible` | Testes que copiam a autoridade são circulares. Exigência de contramodelos e mutação discriminante. Base de MP-K06. |
| **Fato vs. Evidência vs. Observação de Forge** | PR #324, PR #327, `CURRENT_CHECKPOINT.md` | `pre-AOCM / AOCM-compatible` | Separação entre verdade do commit e estado mutável da plataforma/issue. Base de MP-K08 e MP-K11. |
| **Sentinela FORGE_DERIVED** | PR #327, `scripts/lint-canonical-ledger.py` | `AIOps-native / pre-AOCM` | Mecanismo específico para evitar drift de lifecycle em documentos estáticos. Antecedente direto do modelo de freshness. |
| **Earliest Lossy Boundary** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` | `pre-AOCM / AOCM-compatible` | Declaração explícita de onde ocorrem as perdas de contexto na transformação de dados. Base de MP-K03. |
| **Materiality is a Relation, not a Property** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` | `pre-AOCM / AOCM-compatible` | Um achado só é material em relação a uma obrigação ou consumidor específico. Base de MP-K04. |
| **Authority Non-Escalation** | `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`, `AGENTS.md` | `pre-AOCM / AOCM-compatible` | Ter capacidade ou ferramenta não confere autoridade; handoff não transfere grant. Base de MP-K01 e MP-K10. |
| **Ritual de PR e Fases APCP** | PR #33, PR #41, `method/PR_LIFECYCLE.md` | `AOCM-now-supersedes-process-duplication` | O ciclo de 10 fases formalizado no MPACK substitui instruções de ciclo de PR dispersas em várias issues. |

---

## 2. Onde o AOCM Substitui Duplicações e Onde Preserva Owners

### 2.1 O que o AOCM Substitui (Process Duplication)
- Substitui a necessidade de manter múltiplos guias ad-hoc sobre como planejar, isolar claims, testar causalmente e fechar PRs.
- Padroniza a nomenclatura de evidência (`PACKAGE_INTEGRITY_VALID`, `LOCAL_OBSERVATION`, `UNADOPTED`, `ADOPTED_BY_MAINTAINER`).

### 2.2 O que o AOCM Preserva Intacto no Owner Local
- **Owner de `STOP/REDESIGN`:** Continua com exclusividade em `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`. O AOCM não redefine os gatilhos arquiteturais do AIOps.
- **Contratos de Runtime e Segurança:** Continuam com exclusividade em `docs/engineering/PROJECT_OVERLAY.md` e `docs/AGENT_REVIEW_V2_CONTRACTS.md`.
- **Governança de Workflows:** Permanece governada por `.github/AGENTS.md`.
