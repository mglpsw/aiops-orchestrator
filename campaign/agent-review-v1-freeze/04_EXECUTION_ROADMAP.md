# NON_NORMATIVE_CAMPAIGN_ARTIFACT
# Status: V1_C0_CONTRACT_CANDIDATE
# AgentReview v1 — Roadmap de execução até o source candidate final

Este roteiro **não concede autoridade**. Cada slice nasce do `master` resultante do
merge da predecessora, revalida o estado vivo e rederiva obrigações se houver drift.
Uma slice estrutural ativa por vez. Qualificação nunca é empilhada entre PRs.

## Sequência

| # | Slice | Owner | Tipo | Terminal esperado | Risco de parada previsto |
|---|---|---|---|---|---|
| 0 | V1-C0 contract freeze | #221 | docs/contrato; sem comportamento | `V1_C0_CONTRACT_READY` | baixo |
| 1 | V1-C1 coverage truth | #232 | comportamento v1 (planner + propagação) | `V1_C1_COVERAGE_TRUTH_READY` | médio: decisão de semântica "complete" (tier solicitado vs revisão textual) deve sair do texto de #232, não ser inventada |
| 2 | V1-C2 claim coverage / limitation propagation | #221 (#805) | comportamento v1 mínimo | `V1_C2_SEMANTIC_CLAIM_COVERAGE_READY` | **alto: `STOP_OWNER_BOUNDARY` provável** (ver abaixo) |
| 3 | V1-C3 materiality | #343 | comportamento v1 (normalizer) | `V1_C3_MATERIALITY_READY` | médio: aboutness exige material revisado como nova entrada do parser; mudança de response contract toca golden fixtures congeladas |
| 4 | V1-C4 non-vacuous result / TS1 | #307 | comportamento v1 (gate/synth) | `V1_C4_GATE_NONVACUITY_READY` | médio; CM-C5-01/02 já reproduzidos |
| 5 | V1-C5 egress disposition | #315 | decision contract primeiro | `V1_C5_EGRESS_DISPOSITION_READY` ou `BLOCKED_BY_EXPLICIT_HUMAN_DECISION` | **alto: `STOP_UNRESOLVED_POLICY` provável** |
| 6 | V1-C6 final conformance | #221 | corpus consolidado; sem feature | `V1_FINAL_SOURCE_CANDIDATE_READY` | depende de 1–5 |

## Notas por slice

### V1-C1 (#232)
Seguir a "proposta mínima" do próprio #232: (1) generalizar a exclusão com
`reason_code` distinto para não-must; (2) dar hunks sintéticos aos fixtures cujo
assunto não é disponibilidade de hunk; (3) revalidar `_plan_status` →
`chunk_result_parser` → `final_synthesizer` → `quality_gate`. O e2e contract test
que hoje afirma `passed` com arquivos sem hunk (CM-C1-01) precisa ser reconciliado
explicitamente no PR. Espelhar qualquer mudança de `chunk.limitations` na projeção do
planner (`payload_cost_model.py:1488,1492`).

### V1-C2 (claim coverage)
Fatos observados que tornam `STOP_OWNER_BOUNDARY` provável para a parte **estruturada**:

- não existe objeto claim/must-hold estruturado com id no v1;
- `must_hold` é `list[str]` sem id e é o primeiro item descartado sob budget;
- nenhum slot de resposta por claim; nenhum consumidor downstream;
- a claim de #805 existia apenas no corpo do PR, que o v1 nunca transporta;
- nenhum `must_hold` do target cobria fidelidade de par.

Fechar a propriedade exigiria: novo objeto admitido (autoria target-side), extensão
do response contract (golden fixtures congeladas), e consumo em parse/synth/gate —
isso é nova arquitetura de claim, que #221/#357 reservam ao sucessor. Menor
alternativa compatível já identificada, candidata a ficar **dentro** do v1:

1. honestidade de limitação para contratos não achatáveis (OBL-V1C2-02 / CM-C2-02);
2. propagação ao gate das limitações que impedem avaliar obrigação (OBL-V1C4-01),
   em especial `aux_context`/`must_hold` omitido por budget;
3. declarar como non-claim explícito que o v1 não produz claim coverage e que
   `approve_*` do v1 não afirma satisfação de claims do PR.

A decisão entre "alternativa mínima e seguir" e "parar a travessia" pertence à
slice V1-C2 após reconciliação de owner; este roteiro não a antecipa. Se for STOP, a
travessia para ali (regra "não contornar STOP").

### V1-C3 (#343)
Anexar predicados determinísticos em `finding_normalizer._normalize_finding`
reutilizando `_downgrade_or_reject` (confirmado → risco `downgraded_finding`, sem
novo canal). Aboutness exige que o parser receba o material revisado (payload do
chunk ou diff) — mudança de interface de CLI a justificar. O positive control
PC-C3-01 é obrigatório antes e depois.

### V1-C4 (#307)
Reproduzir TS1 independentemente (o trust set de #275 está ausente em master; a
forma alcançável é CM-C5-01/02). Não portar a asserção vacuosa de #275. Mutação
bounded conforme #307.

### V1-C5 (#315)
Decision contract antes de código. Uma garantia estrutural de "nenhum source cru" é
incompatível com a função da L-CHUNK (revisar código exige transmiti-lo). Opção A só
cabe como fechamento **mais forte e compatível** (p.ex. tornar `output_safe_for_llm`
computado, reduzir superfície da L-LEGACY), e mesmo assim deixa risco residual cuja
aceitação é decisão humana. B e C são reservadas a humano por #221. Não copiar
G2C/`structural_egress_projection_v2.py`.

### V1-C6
Corpus consolidado: coverage truth, claim coverage (ou non-claim), limitation
propagation, materiality, TS1/non-vacuity, egress disposition, publication fidelity
(evidência histórica + limitação), contraexemplos AgentEscala e positive controls.
Reconstrução `Claim → mechanism → consumer → countermodel → discriminator →
remaining limitation` para cada entrada de `01_CLAIM_LEDGER.json`.

## Fronteira após o source candidate

```text
maintenance release (source v1 JÁ mudou desde v0.22.0: #227, #231) -> STOP_RELEASE_BOUNDARY
-> AgentEscala minimal repin       -> STOP_TARGET_REPIN_BOUNDARY
-> canário natural exato + publication fidelity
-> decisão residual AgentEscala#678
-> support matrix + disable/rollback docs
-> V1_FINAL_FREEZE
```

Nenhuma dessas ações é concedida pelo grant desta travessia. `v0.22.0` não é movida.

## Estado terminal esperado da travessia de source

```yaml
AgentReview_v1:
  role: LEGACY_ADVISORY_BASELINE
  new_features: forbidden
  source_debt: closed_or_explicitly_disposed
  post_merge_review_debt: zero_known_material
  release_status: pending_separate_grant_if_source_changed   # source já mudou
  final_freeze: not_declared_until_release_repin_canary
```
