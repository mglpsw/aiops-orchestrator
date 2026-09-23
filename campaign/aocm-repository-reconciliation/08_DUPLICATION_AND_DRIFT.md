# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Diagnóstico de Integridade, Duplicação e Drift de Conhecimento

**Campanha:** `aocm-repository-reconciliation`  
**Data:** 2026-09-23  
**Repositório:** `mglpsw/aiops-orchestrator`

---

## 1. Checkpoint Stale Ativo no Contexto de Sessão

### Ocorrência Crítica: `docs/engineering/CURRENT_CHECKPOINT.md`
- **Diagnóstico:** O arquivo declara `Status: CANONICAL | CURRENT` com corte temporal em `2026-08-16` e âncora de implementação no commit `0cdb9615637e3e0563d5caa9ef58b25c01c68fdb` (PR #239).
- **Impacto Real:** Este arquivo é importado diretamente pelo `CLAUDE.md` (`@docs/engineering/CURRENT_CHECKPOINT.md`). Consequentemente, sessões de agentes recebem uma foto estática de meados de agosto de 2026, ignorando mais de 15 PRs subsequentes (incluindo as slices críticas #247, #258, #262, #263, #265, #270, #272, #296, #306, #312, #313, #322 e #341).
- **Reconciliação:** O arquivo deve ser formalmente reclassificado como snapshot histórico (`HISTORICAL_CHECKPOINT_20260816`), e a instrução viva não deve confiar em seu texto como verdade corrente.

---

## 2. Conflito de Autoridade de Roadmap

### `docs/AGENT_REVIEW_V2_ROADMAP.md` vs Issue #46
- **Diagnóstico:** `docs/AGENT_REVIEW_V2_ROADMAP.md` possui um cabeçalho explícito declarando `Status: SUPERSEDED -> issue #46`. Contudo, continua presente na árvore de documentação e pode ser lido por novos agentes como especificação normativa.
- **Autoridade Única:** A issue [mglpsw/aiops-orchestrator#46](https://github.com/mglpsw/aiops-orchestrator/issues/46) é a única autoridade normativa do roadmap.
- **Reconciliação:** O arquivo deve manter sua classificação de artefato histórico de planejamento arquivado, sem autoridade para ordenar prioridades atuais.

---

## 3. Duplicação Física por Projeção Materializada

### `evals/agent_review_v2/case_sources/` vs `evals/agent_review_v2/reviewable_corpus/`
- **Diagnóstico:** O censo físico encontrou múltiplos pares de arquivos com hashes SHA-256 idênticos entre estas duas árvores.
- **Causa Raiz:** `reviewable_corpus` é a projeção determinística gerada pelo script `scripts/materialize-benchmark-case.py` a partir de `case_sources`. O pipeline de CI (`ci.yml`) verifica que a projeção em disco permanece byte-idêntica (`--check`).
- **Reconciliação:** A relação é estritamente `Source (case_sources) -> Generator (materialize-benchmark-case.py) -> Generated View (reviewable_corpus)`. Não há duplicate semantic owner; `case_sources` é o owner normativo do caso e `reviewable_corpus` é o artefato de replay.

---

## 4. Projeções CAEM 2.1 Preservadas

### `AGENTS.md`, `CLAUDE.md`, `CAEM_CORE.md`, `PROJECT_OVERLAY.md`
- **Diagnóstico:** Todos os 4 arquivos carregam o comentário de cabeçalho:
  `<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION body_provenance: historical_caem_2_1_projection ... -->`
- **Risco de Manutenção:** O gerador externo que os produziu não reside no repositório AIOps. Edição manual direta causaria drift de proveniência e poderia violar o verificador `scripts/verify-caem-f0-pin.py --check`.
- **Reconciliação:** Foram mantidos intocados. Para Antigravity, foi criado o arquivo de instruções de extensão nativa `GEMINI.md`, eliminando a necessidade de violar a restrição dos arquivos gerados.

---

## 5. Análise da PR #327 e Mecanismos R1–R5

A PR #327 (`feat(engineering): canonical ledger structural invariant linter (#324)`) foi mantida isolada como corpus candidato.

### Avaliação dos Mecanismos R1–R5:
1. **R1 (Lifecycle Claim Fields vs Live Forge):**
   - *Mecanismo:* Impede que documentos duráveis afirmem estados vivos de issues que podem ficar desatualizados, recomendando sentinelas como `FORGE_DERIVED`.
   - *Alinhamento AOCM:* Totalmente compatível com o princípio AOCM de separação entre fato imutável do commit e estado mutável da forge (`Fact != LiveForgeObservation`).
2. **R2 (No Duplicate Mapping Keys in Same Block):**
   - *Mecanismo:* Detecta chaves duplicadas (ex: `issue_312:`) que o parser YAML padrão silencia silenciosamente sobrescrevendo.
   - *Alinhamento AOCM:* Integridade estrutural elementar.
3. **R3 (No Literal Commit SHAs in Current-Axis Fields):**
   - *Mecanismo:* Proíbe que campos declarados como "atuais" embutam SHAs fixos que se tornam obsoletos no momento do merge.
   - *Alinhamento AOCM:* Precursor direto da separação de identidades em MP-K02.
4. **R4 (Exact Count Regex Bounding):**
   - *Mecanismo:* Evita que o texto afirme "exatamente N testes passaram" sem fixtures e contadores verificados.
   - *Alinhamento AOCM:* Controle de evidências e não-invenção de testemunhos (MP-K06).
5. **R5 (Status Vocabulary Conformance):**
   - *Mecanismo:* Restringe os valores de status ao vocabulário canônico do documento.

### Disposição da PR #327:
- A PR #327 não deve ser absorvida ou mesclada durante a campanha de reconciliação de conhecimento.
- Seus mecanismos (especialmente R1 e R3) devem ser preservados como requisitos para o futuro linter do ledger quando a issue #329 (completude do corpus de mutação) for resolvida.
