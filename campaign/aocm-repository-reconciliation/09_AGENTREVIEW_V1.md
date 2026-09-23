# NON_NORMATIVE_RECONCILIATION_ARTIFACT
# Linhagem e Arquitetura de Conhecimento — AgentReview v1

**Campanha:** `aocm-repository-reconciliation`  
**Projeto:** AgentReview (Geração 1)  
**Status Atual:** `RELEASED | MAINTENANCE_FREEZE` (Baseline: `v0.22.0`)

---

## 1. Papel e Escopo do AgentReview v1

O AgentReview v1 é o pipeline determinístico offline de revisão de código originalmente implementado para o AIOps e atualmente consumido em produção pelo `AgentEscala`.

Ele é **congelado para novas funcionalidades**; aceita unicamente correções de segurança, defeitos críticos e compatibilidade de migração.

---

## 2. Pipeline Determinístico e Estrutura de Artefatos

O pipeline opera em CT104 (ou ambiente local isolado) e segue a sequência linear de transição de arquivos:

```text
aiops-intake.json + redaction-report.json
  │
  ▼
semantic-chunk-plan.json
  │
  ▼
pr-brief.json + chunk-payload-manifest.json + chunk-payloads/<chunk_id>.json
  │
  ▼
chunk-results.json
  │
  ▼
final-review.json + final-review.md
  │
  ▼
review-quality-gate.json (AUTORIDADE CANÔNICA DE DECISÃO)
  │
  ▼
review-telemetry.json (Observação pós-gate)
  │
  ▼
[Opcional] false-positive-signatures.json + suggested-contract-updates.yaml
```

---

## 3. Autoridade e Regra de Decisão do Gate

- **Autoridade Canônica:** `review-quality-gate.json` é a **única** autoridade de decisão pós-síntese no v1.
- **Não-substituição por Síntese:** O artefato `final-review.json` é uma síntese textual de observações e não possui autoridade para substituir o gate caso este esteja ausente, corrompido ou inconclusivo.
- **Fail-Closed:** Falhas estruturais de schema ou JSON inválido causam falha imediata da CLI com código de saída não-zero sem emissão de artefato de gate. Quando o gate avalia observações válidas mas encontra severidades bloqueantes ou incerteza, emite o status formal `manual_review_required` (bloqueando publicação automática). O status `manual_required` não existe no schema v1.

---

## 4. Classificação dos Componentes do v1

| Componente | Arquivo / Path | Classificação | Papel e Observações |
|---|---|---|---|
| Esquemas Pydantic v1 | `app/agent_review/schemas.py` | V1 implemented | Define `ReviewIntake`, `PRBrief`, `SemanticChunkPlan`, `FinalReview`, `QualityGateDecision`. |
| Gerador de PR Brief | `app/agent_review/pr_brief.py` | V1 implemented | Produz `pr-brief.json` determinístico e sanitizado. |
| Planejador de Chunks | `app/agent_review/semantic_chunker.py` | V1 implemented | Divide o diff em semantic chunks com limite de tokens. |
| Sintetizador Final | `app/agent_review/final_synthesizer.py` | V1 implemented | Agrega os resultados individuais de chunks em `final-review.json`. |
| Quality Gate v1 | `app/agent_review/quality_gate.py` | V1 implemented | Avalia severidades (P0, P1, P2, P3) e gera `review-quality-gate.json`. |
| Modelo de Custo de Payload | `app/agent_review/payload_cost_model.py` | V1 implemented | Mede limites de tokens para evitar truncamento silencioso. |
| Detector de Falso-Positivo | `app/agent_review/false_positive_signatures.py` | V1 implemented | Observa padrões de comentários ou ruído recorrente. |
| Sugestões de Contrato | `app/agent_review/contract_suggestions.py` | V1 implemented | Produz sugestões `manual_only` para atualização de contratos de target. |
| Identidade de Versão | `app/agent_review/versioning.py` | V1 implemented | Vincula SHA do toolrepo à execução. |
| CLI Wrappers v1 | `scripts/aiops-review-*.py` | V1 compatibility surface | Pontos de entrada executados pelo wrapper do AgentEscala. |
| Migrador de Perfil v1→v2 | `app/agent_review/profile_migration_v1_v2.py` | V1 bridge to V2 | Converte `TargetProfile` v1 em `TargetProfileV2` de maneira idempotente e explícita. |

---

## 5. Dívidas Conhecidas e Issues Abertas do v1

1. **Issue #343:** Exigir consequência material antes de promover observações descritivas de diff.
2. **Issue #315:** Eliminar ou desabilitar explicitamente o egresso de raw-source legado para o Router antes do freeze definitivo.
3. **Issue #307:** Fechar sobrevivente de mutação TS1 e assert de gate vazio (sucessor do U2 Result Coverage Truth).
4. **Issue #232:** Arquivos sem hunks que não exigem revisão (`non-must_review`) ainda relatados como com cobertura total.
5. **Issue #221:** Consolidar GA operacional definitivo, canário pós-merge e encerramento da trilha v1.
6. **Issue #213:** Preservar proveniência e separar disponibilidade de evidência de cobertura de review.

---

## 6. O que o v1 Deixa como Legado Permanente

- O conceito de **Quality Gate Determinístico Fail-Closed**: a separação estrita entre a inferência do LLM (advisory) e a regra de transição consumível pelo orquestrador.
- A sanitização estrita de dados antes do egresso para o Router.
- A disciplina de consumo por commit SHA imutável de 40 caracteres.
