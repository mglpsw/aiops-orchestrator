# NON_NORMATIVE_CAMPAIGN_ARTIFACT
# Status: V1_C0_CONTRACT_CANDIDATE
# AgentReview v1 — Escopo, claim budget final e non-claims

**Repositório:** `mglpsw/aiops-orchestrator`
**Campanha:** `agent-review-v1-freeze`
**Roadmap owner:** [#221](https://github.com/mglpsw/aiops-orchestrator/issues/221)
**Sucessor futuro (fronteira, não escopo):** [#357](https://github.com/mglpsw/aiops-orchestrator/issues/357)
**Subject de derivação:** `master = ab92e89f096b391bc50759afa3d0f6050881633a`, tree `a811df6344ab5ba450b3a1b5fd12cd45d0477552` (observado em 2026-09-30)
**Baseline publicada/consumida:** `v0.22.0 = 2ce1f45768b8779cb48ef8a302d4ed796349f0e5`

**Namespaces:** claims do ledger são `CL-0…CL-7`; obrigações `OBL-CLn-xx`;
countermodels `CM-CLn-xx`; positive controls `PC-CLn`. Os nomes `V1-C0…V1-C6`
designam **somente slices** da travessia (e seus terminais `V1_Cn_*_READY`). Claim
`CL-n` e slice `V1-Cn` não são a mesma coisa.

Este artefato **não altera comportamento** e **não concede autoridade**. Ele fixa o
que o v1 final pode afirmar, que evidência cada afirmação exige, o que a degrada, e o
que o v1 explicitamente não afirma. Toda afirmação sobre o código foi derivada do
subject acima; `CHECKPOINT_SHA != LIVE_BASELINE` — revalidar antes de consumir.

---

## 1. Papel do produto (decisão já tomada em #221, 2026-09-27)

```yaml
role: LEGACY_ADVISORY_BASELINE
future_successor: "#357 Advisory profile sobre core comum"
new_trust_architecture_here: forbidden
new_features: forbidden
```

```text
v1 = dívida material → correção mínima → qualificação → baseline final → rollback legado → freeze
v1 != plataforma futura; v1 != v2 simplificado; v1 != nova trust architecture
```

Política herdada de #221 (comentário `V1_FINAL_DEBT_RECONCILIATION`):

```text
v1 bug                              -> corrigir
v1 arquitetura limitada mas segura   -> documentar / congelar
"seria melhor se o v1 tivesse X"     -> não implementar; pertence ao v2 / #357
```

Qualquer **aceitação de risco** ou **redução funcional** exige decisão humana explícita
(#221: "esta epic não concede dispensas").

## 2. Superfície v1 coberta por este contrato

| Lane | Entrada | Estado observado | Papel |
|---|---|---|---|
| **L-CHUNK** (engine v1) | `scripts/aiops-review-{intake,plan-chunks,build-payloads,parse-chunks,synthesize,quality-gate,telemetry,false-positives}.py` + `app/agent_review/` (não-`_v2`) | ativa em produção no consumer AgentEscala (CT104), pin `v0.22.0` (`AgentEscala@e9cc03ff:.github/workflows/agent-review.yml:18-19`, blob `7fff440c`) | lane principal; objeto primário deste contrato |
| **L-LEGACY** | `scripts/github_agent_review.py` via `.github/workflows/agent-review.yml` (`issue_comment`, `/agent review llm`, `/agent ask`) | armada neste repo (vars/secret presentes); última execução observada 2026-05-04 | lane legada; relevante para egress (#315) |

A inferência da L-CHUNK **não** ocorre neste repositório: os CLIs são offline; o
wrapper do consumer envia cada `chunk-payloads/<id>.json` ao Router. O engine é dono do
conteúdo do payload; o consumer é dono do transporte e da publicação.

## 3. Claim budget final (máximo que o v1 pode sustentar)

Resumo; o ledger completo com producer/consumer/evidência está em
`01_CLAIM_LEDGER.json`.

| Id | Claim | Owner | Estado no subject de derivação |
|---|---|---|---|
| CL-0 | AgentReview v1 é engine de revisão **advisory**; não concede autoridade de merge. | #221 | por design + observado em 2026-09-30: `develop` do consumer sem branch protection e único ruleset `disabled` (nenhum required check); observação datada |
| CL-1 | Coverage positiva significa que o material necessário foi realmente admitido (`PathPresent != MaterialReviewed`). | #232 | **não sustentado** (CM-CL1-01, CM-CL1-02) |
| CL-2 | Claim/must-hold **estruturada e admitida** aplicável recebe estado `covered/partial/not_covered/not_applicable` separado de file coverage. | #221 (comentário #805) — sem issue dedicada | **não sustentado**; primitiva estruturada inexistente (risco `STOP_OWNER_BOUNDARY`); estreitamento da exigência de #805 = `PENDING_HUMAN_DECISION` (§6) |
| CL-3 | Finding confirmado exige mudança observada + relação aplicável + evidência concreta + consequência negativa + aboutness no subject exato. | #343 | **não sustentado** (CM-CL3-*) |
| CL-4 | Limitação que impede avaliar obrigação degrada coverage/confiança dessa obrigação. | #221 (#805) / #232 / #307 | **parcial**: só limitações de nível plan (via `plan.status`/`files_not_covered`) e de resultado alcançam o gate; limitações de payload/brief são descartadas antes dele |
| CL-5 | Resultado bloqueante só surge de blocker adequadamente sustentado; resultado sobrevivente não homologa cobertura que não recebeu. | #307 | **não sustentado** (CM-CL5-01, CM-CL5-02) |
| CL-6 | Egress: proteção/redaction compatível com o contrato histórico (regex/blocklist). **Não** garantia estrutural. | #315 | sustentado **somente** na forma fraca (L-CHUNK: regex + path absoluto/home; L-LEGACY: regex sem redaction de path); disposição pendente |
| CL-7 | O publisher target publica o **mesmo** resultado produzido pelo engine no CT104. | consumer (AgentEscala #802/#803) | `supported_historical`: evidência datada (#803); não prova do próximo HEAD |

## 4. Non-claims explícitos do v1 final

O v1 final **não** declara, e nenhum teste deve ser tornado verde elevando estas claims:

```text
ausência de defeitos
completude semântica universal
proveniência forte de execução
binding exato resultado↔plano↔commit revisado / resistência a replay (plan/results v1 não carregam SHA nem digest)
CI independente confiável
S/E forte
autoridade de nível Assured
merge readiness
garantia estrutural de que nenhum source/path cru alcança o transporte de inferência
CLEAN = prova de correção
file coverage = semantic claim coverage
ausência de finding = satisfação de todo contrato
confiança do LLM = verdade
redaction regex = sanitização completa
output_safe_for_llm = propriedade computada   (é constante True: app/agent_review/cli.py:109)
```

Regras epistêmicas permanentes desta campanha:

```text
Recommendation != Decision        Review != Authority        Artifact != Authority
FileCoverage != SemanticClaimCoverage
ObservedChange != MaterialFinding  ModelFinding != VerifiedRepositoryDefect
GatePassed != FindingsTrue         NoFinding != ProofOfCorrectness
PreviousHeadQualified != CurrentHeadQualified
Supported != Qualified != Integrated != Released != Deployed
LLM output = hipótese advisory; evidência determinística = evidência; grant humano = autoridade
```

## 5. Reconciliação documental feita nesta slice

`campaign/aocm-repository-reconciliation/09_AGENTREVIEW_V1.md` §6 afirmava como
legado permanente "a sanitização estrita de dados antes do egresso para o Router". O
código observado aplica apenas redaction regex/blocklist e envia hunks/paths
relativos crus (CM-CL6-01). A linha foi corrigida para a forma não sobre-afirmada, com
referência a #315. Nenhum outro documento foi reescrito nesta slice.

## 6. Fora de escopo desta campanha

```text
release / tag / publicação de versão        -> STOP_RELEASE_BOUNDARY
repin no consumer                           -> STOP_TARGET_REPIN_BOUNDARY
deploy / CT102 / produção                   -> STOP_PRODUCTION_BOUNDARY
arquitetura Advisory/Assured (#357)         -> fora
v2 / G2C / structural_egress_projection_v2  -> fora (não copiar)
AgentEscala#678 (escopo server-side do token) -> owner target; residual externo
extração NLP genérica de claims do corpo do PR -> proibida pelo task contract da travessia
```

**Tensão registrada, não resolvida aqui.** O comentário de #221 de 2026-09-16
(AgentEscala #805) é a exigência de registro: "claims declaradas/must-hold relevantes
devem ser extraídas do PR/contract pack", fixture #805 "=> finding
material/merge-blocking", e "`contracts_context_not_relevant` deve ser impossível
quando a claim depende do contract pack selecionado". O task contract desta travessia
(registro: https://github.com/mglpsw/aiops-orchestrator/pull/365#issuecomment-5914381014;
confirmado pelo owner em 2026-09-30) restringe os **meios**: texto livre do PR não
vira autoridade determinística e não há NLP genérico de claims. Essa restrição de
meios **não** dispõe da exigência de #805. Qualquer parte da exigência que não possa
ser atendida dentro dos meios permitidos é redução funcional e fica
`PENDING_HUMAN_DECISION` (#221: "esta epic não concede dispensas"); a slice V1-C2
decide entre fechamento, `STOP_OWNER_BOUNDARY` ou pedido de decisão.

Estado observado no subject (fato, não disposição): o v1 não produz claim coverage
e não transporta título/corpo do PR ao payload da L-CHUNK; portanto, hoje, nenhum
`approve_*` do v1 atesta satisfação de claims declaradas no PR. Se isso vira non-claim
final ou é fechado é exatamente a decisão pendente acima.

## 7. Fato de release já estabelecido

A fonte v1 **já mudou** desde `v0.22.0` (independentemente desta campanha):
`da5a03b` (#225/#227) e `ffa3040` (#231, H1-B) alteraram `semantic_chunker.py`,
`chunk_payload_builder.py`, `payload_cost_model.py` (novo), `pr_brief.py`,
`quality_gate.py`, `chunk_result_parser.py`, `scripts/aiops-review-build-payloads.py`,
`scripts/aiops-review-plan-chunks.py`. Além disso `app/agent_review/versioning.py`
(módulo **compartilhado** v1/v2 — seletor de versão de contrato importado por código v2) foi alterado pelo commit v2
`5b94632` (#270) — único commit em `git log 2ce1f457..ab92e89 -- app/agent_review/versioning.py`. O consumer em produção executa `v0.22.0`
e portanto **não** contém essas correções. Uma release de manutenção será
necessária antes do repin — ação fora deste grant (`STOP_RELEASE_BOUNDARY`).
