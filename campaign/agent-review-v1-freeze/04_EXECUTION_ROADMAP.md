# NON_NORMATIVE_CAMPAIGN_ARTIFACT
# Status: V1_C0_CONTRACT_CANDIDATE
# AgentReview v1 — Roadmap de execução até o source candidate final

Este roteiro **não concede autoridade**. Cada slice nasce do `master` resultante do
merge da predecessora, revalida o estado vivo e rederiva obrigações se houver drift.
Uma slice estrutural ativa por vez. Qualificação nunca é empilhada entre PRs.
Cada task contract declara, antes do primeiro patch, a allowlist de paths que a slice pode
modificar; `git diff --name-only` fora dela é parada (`02` → `slice_gates.v1_v2_isolation`).

## Sequência

| # | Slice | Owner | Tipo | Terminal esperado | Risco de parada previsto |
|---|---|---|---|---|---|
| 0 | V1-C0 contract freeze | #221 | docs/contrato; sem comportamento | `V1_C0_CONTRACT_READY` | baixo |
| 1 | V1-C1 coverage truth | #232 | comportamento v1 (planner + propagação) | `V1_C1_COVERAGE_TRUTH_READY` | médio: decisão de semântica "complete" (tier solicitado vs revisão textual) deve sair do texto de #232, não ser inventada |
| 2 | V1-C2 contexto de contratos/packs + honestidade de limitação | #221 + AgentEscala#869 | slice de contrato (esta sincronização) + slice de implementação sucessora (engine genérico) | contrato: `V1_C2_NORMALIZED_SEMANTIC_INTERFACE_DECIDED`; implementação: `V1_C2_CONTRACT_CONTEXT_AND_LIMITATION_READY` | médio: Gate A congela admissão bruta; per-claim #805 adiada pelo owner |
| 3 | V1-C3 materiality | #343 | comportamento v1 (normalizer) | `V1_C3_MATERIALITY_READY` | médio: aboutness exige material revisado como nova entrada do parser; mudança de response contract toca golden fixtures congeladas |
| 4 | V1-C4 non-vacuous result / TS1 | #307 | comportamento v1 (gate/synth) | `V1_C4_GATE_NONVACUITY_READY` | médio; CM-CL5-01/02 já reproduzidos |
| 5 | V1-C5 egress disposition | #315 | decision contract primeiro | `V1_C5_EGRESS_DISPOSITION_READY` ou `BLOCKED_BY_EXPLICIT_HUMAN_DECISION` | **alto: `STOP_UNRESOLVED_POLICY` provável** |
| 6 | V1-C6 final conformance | #221 | corpus consolidado; sem feature | `V1_FINAL_SOURCE_CANDIDATE_READY` | depende de 1–5 |

## Notas por slice

### V1-C1 (#232)
Seguir a "proposta mínima" do próprio #232: (1) generalizar a exclusão com
`reason_code` distinto para não-must; (2) dar hunks sintéticos aos fixtures cujo
assunto não é disponibilidade de hunk; (3) revalidar `_plan_status` →
`chunk_result_parser` → `final_synthesizer` → `quality_gate`. O e2e contract test
que hoje afirma `passed` com arquivos sem hunk (CM-CL1-01) precisa ser reconciliado
explicitamente no PR. Espelhar qualquer mudança de `chunk.limitations` na projeção do
planner (`payload_cost_model.py:1488,1492`).

### V1-C2 — registro histórico supersedido (#221 + AgentEscala#869)

O registro abaixo preserva a decisão anterior e os contraexemplos que levaram à
correção. Ele está `SUPERSEDED_AS_TOTAL_RAW_GRAMMAR`: não é autoridade normativa
para enumerar YAML, metadados ou cantos de parser. A autoridade atual é a seção
**V1-C2 — interface semântica normalizada (AOCM rescope)** abaixo.
**Contrato de registro:** seção "Contrato funcional de V1-C2" do body de #221 (itens 1–6), em resumo:

1. reconciliar o formato realmente fornecido e os consumidores reais com AgentEscala#869 (`rules` aninhadas vs lista na raiz; `packs` mapa vs lista; campos extras perdidos na projeção). YAML válido não prova compatibilidade;
2. distinguir método AOCM, contrato de produto e intenção/claims da slice; fontes com revisão/seção/estado; texto de PR sem autoridade determinística nem dispensa de policy;
3. conteúdo pertinente (não só referências a arquivos) chega ao payload efetivo; seleção genérica no engine, sem hardcode de regra do target e sem compilador novo no target;
4. distinguir **ausente / incompatível / não aplicável / omitido por orçamento**; `must_hold`/aux context ou contrato material removido limita a conclusão correspondente até parse/synth/gate;
5. preservar utilidade: contexto vazio, esconder warnings ou forçar toda revisão a manual não fecham a propriedade;
6. congelar a interface mínima antes do patch; extensões de schema/CLI com compatibilidade, fixtures e justificativa, sem campos que nenhum consumidor lê.

**Ownership:** engine genérico (loader, seleção/aplicabilidade suportada, prompt/payload, orçamento, propagação) = V1-C2 neste repositório; fontes, projeção declarativa, configuração, conformance e adoção do par = AgentEscala#869 (slices A–D). *(Histórico: "o formato compatível é decidido com a C2 após o censo de consumidores (#869-A)" — superado: a interface foi decidida pelo owner em 2026-09-30, ver decisões abaixo; o censo #869-A passa a ser insumo de confirmação dos consumidores target-side, não de decisão.)* Alterações no AgentEscala estão fora do grant desta travessia; o censo pode ser preparado aqui em leitura.

**#805:** requisito de registro preservado (claims/must-hold pertinentes do PR/contract pack; contramodelo #805 com resultado material). Orientação contextual útil ≠ per-claim coverage ≠ obrigação satisfeita. Parte incompatível com o freeze → `STOP_OWNER_BOUNDARY`/`PENDING_HUMAN_DECISION`; nenhuma non-claim fabricada encerra a exigência. *(Histórico: resolvido pela decisão 4 do owner em 2026-09-30 — per-claim adiada; #805 mantido como controles A/B.)*

*Superado em parte:* a "alternativa mínima" anterior (só honestidade do código `contracts_context_not_relevant`) é substituída, porque #221 registra que trocar essa limitação por outra mais honesta, sozinho, não entrega a propriedade. A exposição a `STOP_OWNER_BOUNDARY` da parte **estruturada per-claim de #805** foi resolvida pela decisão 4 do owner (per-claim adiada para #353/#357); `STOP_OWNER_BOUNDARY` aplica-se agora apenas se a implementação exigir ClaimV1/máquina per-claim. Se a slice atingir um STOP, a travessia para ali (regra "não contornar STOP": body de #221, "sem aplicar patches futuros ou contornar STOP", e o task contract da travessia).

*Histórico:* o terminal previsto `V1_C2_SEMANTIC_CLAIM_COVERAGE_READY` é **substituído** por decisão do owner (2026-09-30) por `V1_C2_CONTRACT_CONTEXT_AND_LIMITATION_READY`, que não implica per-claim coverage estruturada.

#### Decisões do owner (adjudicação de 2026-09-30, registrada na PR #367)

1. **Interface de contratos** *(estendida pela seção "Interface completada" abaixo)*: metadados reservados universais são `version/schema_version/updated/system/domain`, nunca identidades. Se existir `rules`, é exclusivamente `LEGACY_FLAT_MODE`: lista limitada de mappings mais metadados reservados; qualquer chave não reservada adicional é mixed-mode rejeitado. Sem `rules`, é `DOMAIN_MAPPING_MODE`: mapping → contrato de domínio, `list[str]` → contrato de lista nomeada, demais shapes não vazios → limitação tipada. Na forma B, a chave de domínio de topo é a identidade do contrato; nenhum `claim_id` autoritativo é inventado para strings `rule:`; projeção limitada `id/description/sections{…}`: identidade de seção preservada quando material; campos semânticos limitados já presentes na fonte não são descartados silenciosamente; é adaptador de contexto, não ClaimV1 nem OGR; sem motor recursivo genérico de YAML. `contracts_context_not_relevant` só após parse suportado + aplicabilidade avaliada + zero contrato aplicável. O AgentEscala **não** é reescrito para a forma plana.
2. **Packs e seleção:** `packs` lista é `LEGACY_PACK_MODE`; mapping é `MAPPING_PACK_MODE`; ausente só é conjunto vazio com `contract_bindings` ausente/vazio. No mapping, chave → `pack_id`, valor mapping, `paths` ausente ou `list[str]`, `domain_contract` string não vazia se presente e `recommended_review_preset` string se presente; erros de entrada/campo de aplicabilidade são source-level incompatíveis, nunca omitidos. Engine: `paths` → aplicabilidade por arquivo com `fnmatch.fnmatchcase(canonical_repo_path, pattern)` case-sensitive (operador congelado do engine; **não** se afirma identidade cross-platform com o `fnmatch.fnmatch` atual do AgentEscala), `domain_contract` → relação explícita pack→contrato. Seleção = pack selecionado por id EXATO ∪ packs cujos `paths` casam arquivos do chunk; depois `domain_contract` e bindings válidos deduplicados selecionam contratos. Binding órfão é estruturalmente inválido; binding válido para contrato ausente é `selected_contract_domain_missing:<pack>:<contract>`. Para mapping, substring difusa ou palavras-chave de grupo semântico não são mecanismo autoritativo; fallback legado pode permanecer para documentos legados.
3. **Orçamento e honestidade:** `RequiredSemanticContext + MinimumReviewableHunkMaterial = NonSilentReviewFloor`. Para contrato APPLICABLE, identidade de contrato/seção, toda string admitida, toda rule, `must_hold` aplicável e campos presentes `invariant/field/expected_value`, bem como escalares não explicativos, são requeridos; `description/rationale/notes` são opcionais. O planner estabelece contexto requerido aplicável e material mínimo de hunk, tenta encaixar ambos, reduz/divide o chunk e tenta de novo; unidade indivisível que não cabe → cobertura parcial/degradada/não conclusiva com limitação tipada visível no gate. `contracts_context_reduced` só para perda opcional; perda requerida e de `must_hold` não permite aprovação limpa. Propagação pelo caminho existente plan → parse → synth → gate; nenhum novo consumidor de manifest salvo inadequação demonstrada.
4. **#805 / per-claim:** `structured_per_claim_coverage: NOT_SUPPORTED_BY_FINAL_V1`, `EXPLICIT_NON_CLAIM_DEFERRED_TO_SUCCESSOR` (#353/#357). #805 permanece como controle A (regressão determinística de transporte/gate) e controle B (avaliação semântica limitada com expectativas pré-definidas, tentativas registradas, sem re-execução até resultado favorável) — `02` OBL-CL2-07.

**AOCM:** orientação de método target-owned; nada de semântica AgentEscala/AOCM hardcoded no engine; AgentEscala#869 possui projeção/seleção de fontes; V1-C2 possui a capacidade genérica de admitir, selecionar, transportar, orçar honestamente e expor perda material. Método AOCM ≠ contrato adotado ≠ especificação proposta ≠ intenção da PR ≠ evidência determinística.

#### Interface completada na revisão pós-Ready da PR #367 (Codex 5372851950, adjudicação do owner)
- **Discriminador de topo de `domain-contracts.yaml`:** chaves reservadas `version`/`schema_version`/`updated`/`system`/`domain` são metadados, nunca contrato. `rules` na raiz seleciona exclusivamente a forma plana legada e só é admitida como lista limitada de rule-objects com esses metadados; chave não reservada junto é mixed-mode rejeitado. Sem `rules`, mapping não reservado é contrato de domínio e `list[str]` é contrato de lista nomeada; qualquer scalar/container não vazio → `contracts_context_unsupported_shape`. Sem recursão. Detalhe em `02` OBL-CL2-02.
- **`review-packs.yaml`:** `version`/`schema_version`/`updated` são metadados; `packs` lista é legado e mapping é modo atual; `packs` ausente só é vazio com bindings ausentes/vazios. Todo item legacy ou valor mapping tem forma determinística; `paths` e `domain_contract` malformados são incompatibilidade source-level e jamais irrelevância. `contract_bindings` ausente → `{}`, `mapping[str, list[str]]` com chaves/identidades não vazias e sem órfãos → admitido, malformado → `contracts_context_unsupported_shape:review_packs`; `EffectiveContractRefs(pack_id) = {pack.domain_contract if present} ∪ contract_bindings.get(pack_id, [])`, deduplicado; binding estruturalmente válido que não resolve → `selected_contract_domain_missing:<pack>:<contract>`; valores concretos pertencem ao AgentEscala#869.
- **Ausência:** slots conhecidos `.aiops/domain-contracts.yaml` / `.aiops/review-packs.yaml` têm estado `present_valid|absent|invalid`; ausência é tipada, nunca `not_relevant`. `RequiredForChunk` é separado: domínio requerido por dependência explícita ou `EffectiveContractRefs`; packs requerido por seleção explícita ou relação pack/path necessária. Só `absent|invalid + required` degrada (CM-CL2-03, família 14).

#### Slice de implementação sucessora (não autorizada por esta sincronização)
Começa do `master` resultante desta slice e **rederiva** paths exatos. Famílias esperadas (sujeitas a leitura viva): `payload_cost_model.py`, `chunk_payload_builder.py`, `semantic_chunker.py`, testes v1 pertinentes, docs de reason codes. Famílias RED/GREEN obrigatórias:

1. mapping de domínios hoje achata vazio → forma suportada emite conteúdo do contrato aplicável;
2. review-packs mapping hoje achata vazio → mapping preservado no target e interpretado pelo engine;
3. pack selecionado/casado por path com `domain_contract: calendar` → conteúdo `calendar` chega ao chunk pertinente;
4. chunk irrelevante → `calendar` não é injetado só por existir;
5. shape não suportado e não vazio → limitação de incompatibilidade, nunca "not relevant";
6. pack selecionado aponta domínio ausente → limitação tipada degradante;
7. chunk quase cheio hoje perde must_hold/contratos antes de hunks → planner divide/reduz mantendo o piso semântico requerido;
8. conflito de orçamento indivisível → partial/degraded/non-conclusive, nunca aprovação limpa;
9. redução só de detalhe opcional → pode continuar útil sem forçar manual;
10. controle positivo → hunk + contexto suficientes continuam revisáveis/conclusivos;
11. forma mapping do AgentEscala permanece compatível com seus consumidores target-side;
12. forma plana legada continua suportada;
13. sem regressão v2 via módulos compartilhados;
14. fonte ausente (CM-CL2-03 A/B): mesmo slot ausente + `RequiredForChunk=true` → limitação tipada degradante e gate não conclusivo; slot ausente + `RequiredForChunk=false` → estado ausente tipado, mas controle positivo conclusivo; nunca `contracts_context_not_relevant`;
15. gramática de domínio: metadados `version/schema_version/updated/system/domain`, `LEGACY_FLAT_MODE` com `rules` válido, `DOMAIN_MAPPING_MODE` mapping e `list[str]`, mixed-mode, regras/seção malformadas e scalar/container não suportado; cada RED é shape-level, cada GREEN preserva fixture legado;
16. totalidade de review-packs: `packs` ausente/vazio/lista/mapping, item/campo de paths/domain contract malformado, bindings ausentes/válidos/malformados/órfãos e binding para contrato ausente. RED focal observa seleção/limitação; GREEN observa pack aplicável exato e pack irrelevante sem contrato espúrio.

*(Histórico, superado pela revisão pós-Ready da PR #367: o item aberto sobre `response_model_rules` foi resolvido pelo owner em #221 5920435231/5920712897 e está congelado no discriminador de topo de `02` OBL-CL2-02 e na família 15.)*

Para cada novo predicado/reason: mecanismo ausente → RED focal; presente → GREEN focal; controle positivo GREEN. Usar o estado intermediário mais focal disponível, não só o veredito final.

**Stop conditions:** `STOP_OWNER_BOUNDARY` (exige ClaimV1/máquina per-claim), `STOP_TARGET_COMPATIBILITY` (única saída é reescrever packs/contratos do AgentEscala), `STOP_CONTRACT_CONFLICT` (autoridade de regra ambígua ou adotado vs proposto sem resolução), `STOP_SCHEMA_EXPANSION` (novo schema/versão pública quando a propagação plan-level resolveria), `STOP_V2_COUPLING` (copiar/modificar Assured/OGR por conveniência), `STOP_POSITIVE_CONTROL` (honestidade só tornando toda revisão afetada manual), `STOP_SUBJECT_DRIFT`. Não contornar STOP enfraquecendo a claim.

### V1-C2 — interface semântica normalizada (AOCM rescope, normativa para PR #367)

O terminal anterior `V1_C2_CONTRACT_INTERFACE_DECIDED` permanece histórico e é
substituído por `V1_C2_NORMALIZED_SEMANTIC_INTERFACE_DECIDED`.

```text
Raw Target Source
-> Source Admission/Parsing
-> Normalized V1 Contract Context
-> Applicability + Explicit Relations
-> RequiredSemanticKernel + Budget
-> Payload -> Deterministic Loss Propagation -> Parse -> Synth -> Quality Gate
```

Esta PR decide uma álgebra finita: `JsonScalar`; `SemanticValue {key,value}`;
`TextItem {kind,text}` ou `RuleItem {kind,rule,qualifiers,rationale?}`;
`NormalizedSection {section_id,items}`; `NormalizedContract {contract_id,
description?,semantic_values,sections}`; e `NormalizedPack {pack_id,paths,
contract_refs,description?,review_preset?}`. Contratos, seções, valores, packs e
referências têm ordenação canônica; itens preservam a ordem-fonte. O censo de `02`
classifica cada campo observado e legado, inclusive os metadados que não participam
da semântica normalizada. Não há `ClaimV1`, estado per-claim, recursão semântica
arbitrária, mapa residual ou autoridade independente para metadata crua.

Aplicabilidade é `selected pack id` exato união
`fnmatchcase(canonical target-relative path, pattern)`. Referências efetivas são
`domain_contract` união `additional_contract_refs`, deduplicadas. Relação ausente não
introduz contrato; referência explícita que não resolve é não conclusiva.

O núcleo requerido é identidade de contrato/seção, `TextItem.text`, `RuleItem.rule`,
todo qualificador, todo valor semântico e `must_hold` aplicável. `description`,
`rationale` e contexto explicativo de pack são opcionais. Material mínimo de hunk é
o bloco completo e intacto de diff unificado por arquivo; amostra, cabeçalho ou hunk
truncado nunca contam. O planner reduz opcionais, mantém kernel+hunks, repacota e,
se um singleton não couber, marca o arquivo não coberto/não conclusivo. Perda
opcional pode ser conclusiva; perda requerida, de `must_hold` ou de hunk é degradada.

`not_relevant` só é possível após avaliação válida com `ApplicableContractSet` e
`ApplicableNormalizedPackContext` ambos vazios; pack aplicável sem relação e sem
contexto pode ser irrelevante. Os P1–P9
e CM-NORM/CM-ABS/CM-HUNK/CM-PACK em `02`/`03` são os controles obrigatórios desta
decisão; Gate A continua sendo a única autoridade para gramática bruta e grafias
serializadas de reason codes.

Proveniência V1 mínima: `source_kind` (`domain_contracts|review_packs`),
`source_path` canônico relativo ao target, `source_state`
(`present_valid|absent|invalid`) e identidade normalizada de contrato/seção quando
aplicável. SHA/revisão exata fica na qualificação Gate C, não no payload V1.

#### Gate A — RawSourceAdmissionContract (sucessor, não autorizado nesta PR)

Antes de patch runtime, congelar a gramática delimitada contra blobs atuais
AgentEscala, ambos os fixtures legados commitados, chaves duplicadas, raiz malformada
e packs/bindings malformados. Cada fixture admitido deve ter uma e só uma projeção
normalizada; cada rejeitado deve produzir resultado tipado invalid/unsupported, nunca
vazio/não relevante silencioso. Serialização ambígua (inclusive chave duplicada) é
inválida antes da normalização. A tarefa sucessora escolhe e testa uma única forma
serializada para as classes: `SOURCE_ABSENT`, `SOURCE_INVALID_OR_UNSUPPORTED`,
`SELECTED_CONTRACT_MISSING`, `OPTIONAL_CONTEXT_REDUCED`,
`REQUIRED_CONTEXT_OMITTED`, `MUST_HOLD_OMITTED`.

Gate A qualifica parser/admissão; AgentEscala#869 é Gate B; o par exato é Gate C.
Nenhum deles é concedido por esta PR.

### V1-C3 (#343)
Anexar predicados determinísticos em `finding_normalizer._normalize_finding`. **O
canal de saída é decisão aberta:** `_downgrade_or_reject` gera `NormalizedRisk`, e
qualquer risk escala o veredito para `approve_with_required_followup`; `rejected`
leva a `approve_with_minor_notes`; não existe canal neutro de observação. A slice não
pode "corrigir" um P3 tornando o resultado mais estrito, e o discriminador deve
observar a partição confirmed/risk/rejected, não só o veredito. Aboutness exige que o parser receba o material revisado (payload do
chunk ou diff) — mudança de interface de CLI a justificar. O positive control
PC-CL3-01 é obrigatório antes e depois.

### V1-C4 (#307)
Reproduzir TS1 independentemente na forma literal (CM-CL5-03: `ChunkResults` com
`schema_id`/`target_repo` adulterados após construção via API Python → blocker
confiável; o trust set de #275 está ausente em master). CM-CL5-01/02 (já
reproduzidos) são countermodels adicionais de result coverage no escopo de #307, não
TS1. Não portar a asserção vacuosa de #275. Mutação
bounded conforme #307.

### V1-C5 (#315)
Decision contract antes de código. Uma garantia estrutural de "nenhum source cru" é
incompatível com a função da L-CHUNK (revisar código exige transmiti-lo). Opção A só
cabe como fechamento **mais forte e compatível** (p.ex. tornar `output_safe_for_llm`
computado, reduzir superfície da L-LEGACY), e mesmo assim deixa risco residual cuja
aceitação é decisão humana. B e C são reservadas a humano por #221. Não copiar
G2C/`structural_egress_projection_v2.py`. `redaction.py` é importado diretamente por cinco módulos v2 e pelo script de conformance v2, e
alcança transitivamente quase toda a lane v2 via `contracts_v2`: qualquer mudança nele cai na
regra de módulo compartilhado de `02` (`slice_gates.v1_v2_isolation`).

### V1-C6
Corpus consolidado: coverage truth, consumo de contexto (CL-2) com os controles #805 A/B (per-claim adiada pelo owner para #353/#357), limitation
propagation, materiality, TS1/non-vacuity, egress disposition, publication fidelity
(evidência histórica + limitação), contraexemplos AgentEscala e positive controls.
Reconstrução `Claim → mechanism → consumer → countermodel → discriminator →
remaining limitation` para cada entrada de `01_CLAIM_LEDGER.json`.

## Decisões de freeze registradas em #221 (checkpoint 2026-09-30T20:07Z)

- #232, #343 e #307 são blockers duros do freeze; #315 exige disposição explícita (fix, residual nomeado aceito, ou desabilitar/substituir a lane).
- AgentEscala#871 (HTTP 429 sem retry / sem `Retry-After`) é bugfix target-side limitado; entra na C6 consolidada; falha persistente continua virando revisão manual.
- Non-features do v1: `json_schema` no provider, reparo/retry semântico de saída malformada, nova estratégia de modelo/preset, nova arquitetura de agrupamento semântico, receipts/trust architecture v2, OGR/impact-context.
- PR grande/evidência pesada: `chunk_budget_exceeded`/plano parcial não é blocker automático quando falha fechado para revisão manual. Exigência é **veracidade**, não escalabilidade.

**Gate terminal de freeze registrado no checkpoint de #221:** #232/#343/#307/#315 com disposições terminais e evidência exact-subject; par de contexto da C2 qualificado; AgentEscala#871 corrigida (mesmo endpoint/preset/payload) ou aceita explicitamente com evidência operacional; release final imutável e reproduzível; AgentEscala consome exatamente esse par engine+config em canário controlado; 429/504/schema-inválido/budget-excedido permanecem fail-closed sem fabricar cobertura/readiness; rollback/suporte/limitações documentados.

## Fronteira após o source candidate

```text
maintenance release (source v1 JÁ mudou desde v0.22.0: #227, #231) -> STOP_RELEASE_BOUNDARY
-> repin mínimo do PAR engine+pack/config no AgentEscala (#869-D) -> STOP_TARGET_REPIN_BOUNDARY
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
