# NON_NORMATIVE_CAMPAIGN_ARTIFACT
# Status: V1_C0_CONTRACT_CANDIDATE
# AgentReview v1 — Escopo, claim budget final e non-claims

**Repositório:** `mglpsw/aiops-orchestrator`
**Campanha:** `agent-review-v1-freeze`
**Roadmap owner:** [#221](https://github.com/mglpsw/aiops-orchestrator/issues/221)
**Sucessor futuro (fronteira, não escopo):** [#357](https://github.com/mglpsw/aiops-orchestrator/issues/357)
**Subject de derivação:** `master = ab92e89f096b391bc50759afa3d0f6050881633a`, tree `a811df6344ab5ba450b3a1b5fd12cd45d0477552` (observado em 2026-09-30)
**Baseline publicada/consumida:** `v0.22.0 = 2ce1f45768b8779cb48ef8a302d4ed796349f0e5`

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
| **L-CHUNK** (engine v1) | `scripts/aiops-review-{intake,plan-chunks,build-payloads,parse-chunks,synthesize,quality-gate,telemetry,false-positives}.py` + `app/agent_review/` (não-`_v2`) | ativa em produção no consumer AgentEscala (CT104), pin `v0.22.0` | lane principal; objeto primário deste contrato |
| **L-LEGACY** | `scripts/github_agent_review.py` via `.github/workflows/agent-review.yml` (`issue_comment`, `/agent review llm`, `/agent ask`) | armada neste repo (vars/secret presentes); última execução observada 2026-05-04 | lane legada; relevante para egress (#315) |

A inferência da L-CHUNK **não** ocorre neste repositório: os CLIs são offline; o
wrapper do consumer envia cada `chunk-payloads/<id>.json` ao Router. O engine é dono do
conteúdo do payload; o consumer é dono do transporte e da publicação.

## 3. Claim budget final (máximo que o v1 pode sustentar)

Resumo; o ledger completo com producer/consumer/evidência está em
`01_CLAIM_LEDGER.json`.

| Id | Claim | Owner | Estado no subject de derivação |
|---|---|---|---|
| V1-C0 | AgentReview v1 é engine de revisão **advisory**; não concede autoridade de merge. | #221 | sustentado por design; ver non-claims |
| V1-C1 | Coverage positiva significa que o material necessário foi realmente admitido (`PathPresent != MaterialReviewed`). | #232 | **não sustentado** (CM-C1-01, CM-C1-02) |
| V1-C2 | Claim/must-hold **estruturada e admitida** aplicável recebe estado `covered/partial/not_covered/not_applicable` separado de file coverage. | #221 (comentário #805) — sem issue dedicada | **não sustentado**; primitiva estruturada inexistente (risco `STOP_OWNER_BOUNDARY`) |
| V1-C3 | Finding confirmado exige mudança observada + relação aplicável + evidência concreta + consequência negativa + aboutness no subject exato. | #343 | **não sustentado** (CM-C3-*) |
| V1-C4 | Limitação que impede avaliar obrigação degrada coverage/confiança dessa obrigação. | #221 (#805) / #232 / #307 | **parcial**: só `must_review` + `critical_pr` degradam o gate |
| V1-C5 | Resultado bloqueante só surge de blocker adequadamente sustentado; resultado sobrevivente não homologa cobertura que não recebeu. | #307 | **não sustentado** (CM-C5-01, CM-C5-02) |
| V1-C6 | Egress: proteção/redaction compatível com o contrato histórico (regex/blocklist). **Não** garantia estrutural. | #315 | sustentado **somente** na forma fraca; disposição pendente |
| V1-C7 | O publisher target publica o **mesmo** resultado produzido pelo engine no CT104. | consumer (AgentEscala #802/#803) | evidência histórica datada; não prova do próximo HEAD |

## 4. Non-claims explícitos do v1 final

O v1 final **não** declara, e nenhum teste deve ser tornado verde elevando estas claims:

```text
ausência de defeitos
completude semântica universal
proveniência forte de execução
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
extração determinística de claims a partir de texto livre do PR
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
relativos crus (CM-C6-01). A linha foi corrigida para a forma não sobre-afirmada, com
referência a #315. Nenhum outro documento foi reescrito nesta slice.

## 6. Fora de escopo desta campanha

```text
release / tag / publicação de versão        -> STOP_RELEASE_BOUNDARY
repin no consumer                           -> STOP_TARGET_REPIN_BOUNDARY
deploy / CT102 / produção                   -> STOP_PRODUCTION_BOUNDARY
arquitetura Advisory/Assured (#357)         -> fora
v2 / G2C / structural_egress_projection_v2  -> fora (não copiar)
AgentEscala#678 (escopo server-side do token) -> owner target; residual externo
extração NLP de claims do corpo do PR        -> proibida
```

## 7. Fato de release já estabelecido

A fonte v1 **já mudou** desde `v0.22.0` (independentemente desta campanha):
`da5a03b` (#225/#227) e `ffa3040` (#231, H1-B) alteraram `semantic_chunker.py`,
`chunk_payload_builder.py`, `payload_cost_model.py` (novo), `pr_brief.py`,
`quality_gate.py`, `chunk_result_parser.py`. O consumer em produção executa `v0.22.0`
e portanto **não** contém essas correções. Uma release de manutenção será
necessária antes do repin — ação fora deste grant (`STOP_RELEASE_BOUNDARY`).
