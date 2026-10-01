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
| 2 | V1-C2 contexto de contratos/packs + honestidade de limitação | #221 + AgentEscala#869 | slice de contrato (esta sincronização) + slice de implementação sucessora (engine genérico) | requisitos: `V1_C2_REQUIREMENTS_AND_INVARIANTS_FROZEN`; implementação: `V1_C2_CONTRACT_CONTEXT_AND_LIMITATION_READY` | médio: Gate A congela admissão bruta; per-claim #805 adiada pelo owner |
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
**V1-C2 — requisitos e invariantes congelados (autoridade atual)** abaixo. A seção de
interface semântica normalizada é NON-NORMATIVE HISTORY.
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
16. totalidade de review-packs: `packs` ausente/vazio/lista/mapping, item/campo de paths/domain contract malformado, bindings ausentes/válidos/malformados/órfãos e binding para contrato ausente. RED focal observa seleção/limitação; GREEN observa pack aplicável exato e, separadamente, pack que NÃO é aplicável ao chunk sem injeção de contrato espúrio.
17. seleção explícita de pack não resolvida: `selected_contract_pack` não vazio sem match exato/legacy-compatible → `SELECTED_PACK_MISSING`, degradante/não conclusivo, nunca `not_relevant`; controles positivos cobrem match exato e `calendar -> agentescala-calendar` legado.
18. compatibilidade completa de selector legado (A2): comparação case-insensitive; match por igualdade de id, igualdade de description, substring em id ou substring em description; todos os matches exatos resolvidos são preservados. Controles incluem `calendar -> agentescala-calendar`, `aiops -> agentescala-aiops`, description alias e múltiplos matches.
19. legacy rule pattern semantics: A2 normaliza patterns (trim/drop-empty/sanitize/dedupe/sort); A3 recebe somente patterns canônicos e faz final `*` = prefix match removendo só o `*`, demais patterns = substring match. A3 nunca repete trim/drop/sanitize. Não usar `fnmatchcase` para esse operador legado.
20. semantic-context must_hold applicability: A2 normaliza scope/change_type/contract_pack/must_hold e resolve pack token; A3 aplica scope (empty/global/all/document global; otherwise substring/tokens sobre canonical paths) e, quando contract_pack existe, exige interseção com ApplicablePackSet. Unresolved pack -> SELECTED_PACK_MISSING.
21. fallback legado por semantic-group (A3): preservar `_relevance_keywords` atual e substring sobre id+description somente para itens legacy; mapping/domain mode não usa keyword como autoridade quando relações explícitas existem.
22. `semantic-context.change_type` é advisory opcional: não filtra must_hold; loss-only -> `OPTIONAL_CONTEXT_REDUCED`, mantendo must_hold aplicável requerido.
23. legacy flat rule identity (A2): cada `{id,description}` admitido -> um `NormalizedContract` com `contract_id=id`, section `rules`, `RuleItem.rule=description`; `contract:<id>` preservado; id/description malformados -> unsupported.
24. selector preprocessing legado (A2): general selection lê `file-diff-context.contract_pack`; se ausente/blank usa alias `file-diff-context.pack`; ambos recebem clean-text/trim e `contract_pack` vence quando ambos estão presentes. `semantic-context.contract_pack` NÃO popula o general selector: é constraint separada de must_hold. Depois aplica-se o predicate legacy congelado.
25. pattern normalization (A2) / matching (A3): A2 trim/drop-empty/sanitize/dedupe/sort; A3 só prefix por `*` final ou substring.
26. `review_packs` requiredness: `selected_contract_pack` OU `semantic-context.contract_pack` não vazio tornam o source required antes da resolução.
27. reserved review-pack metadata: `version/schema_version/updated` são metadata; `packs` e `contract_bindings` são structural reserved keys.
28. resolved legacy pack multiplicity: A2 entrega o conjunto completo `resolved_pack_ids`; A3 avalia TODOS os resolved ids, nunca escolhe um arbitrariamente.
29. provenance payload: `ProvenanceV1 {source_kind,source_path,source_state}` é requerido e budgeted; perda -> `REQUIRED_CONTEXT_OMITTED`; revision exata fica no Gate C.
30. include-all sentinels: `target_profile:domain_contracts` e `target_profile:review_packs` preservam current legacy include-all behavior com controles focais.
31. selector source precedence: general selection = `file-diff-context.contract_pack`, fallback alias `file-diff-context.pack`; `semantic-context.contract_pack` é somente constraint de must_hold e não substitui o general selector.
32. mapping/domain mode nunca recebe keyword fallback, mesmo relationless; semantic-group keywords são compatibility-only de legacy mode.
33. applicable `NormalizedPack.contract_refs` é required advisory relation context; perda -> `REQUIRED_CONTEXT_OMITTED`.
34. Control B directional criterion: bad arm deve atingir adverse-signal material pré-declarado e corrected arm não reproduzir o mesmo sinal; seeded pair ou K-of-N repeated pairs com K/N congelados antes do run.
35. legacy-flat identity: cada `{id,description}` -> um `NormalizedContract(contract_id=id, description preservada, section=rules, RuleItem.rule=description)`; id não é qualifier duplicado; `contract:<id>` preservado.
36. duplicate legacy contract ids: ids limpos duplicados em LEGACY_FLAT_MODE -> SOURCE_INVALID_OR_UNSUPPORTED antes da normalização; sem dedupe/winner silencioso.
37. SelectionResolution states: `not_requested|resolved|unresolved`; `resolved_pack_ids` é o conjunto completo e A3 avalia todos os ids.
38. ApplicablePackSet union completo: resolved selection + mapping path matches + legacy `target_profile:review_packs` include-all + legacy exact `contract:<id>` pack refs + legacy semantic-group fallback.
39. `target_profile:review_packs` sentinel torna review_packs RequiredForChunk; fonte ausente/inválida -> CM-CL2-04, non-conclusive.
40. missing explicit contract target: applicable pack ref para contrato ausente -> CM-PACK-04 / SELECTED_CONTRACT_MISSING; counterpart com contrato presente resolve.

*(Histórico, superado pela revisão pós-Ready da PR #367: o item aberto sobre `response_model_rules` foi resolvido pelo owner em #221 5920435231/5920712897 e está congelado no discriminador de topo de `02` OBL-CL2-02 e na família 15.)*

Para cada novo predicado/reason: mecanismo ausente → RED focal; presente → GREEN focal; controle positivo GREEN. Usar o estado intermediário mais focal disponível, não só o veredito final.

**Stop conditions:** `STOP_OWNER_BOUNDARY` (exige ClaimV1/máquina per-claim), `STOP_TARGET_COMPATIBILITY` (única saída é reescrever packs/contratos do AgentEscala), `STOP_CONTRACT_CONFLICT` (autoridade de regra ambígua ou adotado vs proposto sem resolução), `STOP_SCHEMA_EXPANSION` (novo schema/versão pública quando a propagação plan-level resolveria), `STOP_V2_COUPLING` (copiar/modificar Assured/OGR por conveniência), `STOP_POSITIVE_CONTROL` (honestidade só tornando toda revisão afetada manual), `STOP_SUBJECT_DRIFT`. Não contornar STOP enfraquecendo a claim.

### V1-C2 — interface semântica normalizada (histórico supersedido; requisitos preservados)

**Status desta seção:** NON-NORMATIVE HISTORY. As formas/algoritmos descritos abaixo são
registro da convergência e requisitos de entrada do Gate A; não são autoridade para
afirmar que normalização, serialization/cost ou packing já foram implementados ou
qualificados. A autoridade documental corrente está na seção de requirements freeze
mais abaixo.

Historicamente, `V1_C2_CONTRACT_INTERFACE_DECIDED` foi substituído por
`V1_C2_NORMALIZED_SEMANTIC_INTERFACE_DECIDED`; ambos estão agora supersedidos pelo
terminal corrente `V1_C2_REQUIREMENTS_AND_INVARIANTS_FROZEN`.

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
`fnmatchcase(canonical target-relative path, pattern)`. Referências efetivas normalizadas são a união exata-deduplicada de
`pack.domain_contract` (quando presente) com `contract_bindings.get(pack_id, [])`. Relação ausente não
introduz contrato; referência explícita que não resolve é não conclusiva.

O núcleo requerido é identidade de contrato/seção, `TextItem.text`, `RuleItem.rule`,
todo qualificador, todo valor semântico e `must_hold` aplicável. `description`,
`rationale` e contexto explicativo de pack são opcionais. Material mínimo de hunk é
o bloco completo e intacto de diff unificado por arquivo; amostra, cabeçalho ou hunk
truncado nunca contam. O planner reduz opcionais, mantém kernel+hunks, repacota e,
se um singleton não couber, marca o arquivo não coberto/não conclusivo. Perda
opcional pode ser conclusiva; perda requerida, de `must_hold` ou de hunk é degradada.

`not_relevant` só é possível após avaliação válida com `ApplicablePackSet` e
`ApplicableContractSet` ambos vazios e sem seleção explícita não resolvida. Todo pack
aplicável mantém `pack_id` como contexto requerido, mesmo sem relação, description ou
review_preset; portanto um pack aplicável nunca vira irrelevante por perda de contexto opcional. Os P1–P9
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
serializada para as classes: `SOURCE_ABSENT`, `SOURCE_INVALID_OR_UNSUPPORTED`, `SELECTED_PACK_MISSING`,
`SELECTED_CONTRACT_MISSING`, `OPTIONAL_CONTEXT_REDUCED`,
`REQUIRED_CONTEXT_OMITTED`, `MUST_HOLD_OMITTED`.

Gate A qualifica parser/admissão; AgentEscala#869 é Gate B; o par exato é Gate C.
Nenhum deles é concedido por esta PR.

### V1-C2 — requisitos e invariantes congelados (autoridade atual)

A autoridade documental corrente é:

```text
V1_C2_REQUIREMENTS_AND_INVARIANTS_FROZEN
RequirementsFrozen != ExecutableContractQualified
```

A PR #367 congela requisitos semânticos, non-claims, classes de compatibilidade,
positive controls, famílias de countermodels e **owners executáveis**. Ela não afirma
que documentação já qualifica parser, compatibilidade legada, normalização, custo,
packing/repacking ou propagação runtime.

#### Gate A — C2ExecutableContract

Antes do primeiro patch runtime, o task contract executável deve congelar e provar:

```text
A1 RawSourceAdmission
A2 LegacyCompatibility + Normalization
A3 Applicability + explicit relation resolution
A4 Canonical cost + deterministic budget/packing
A5 Required-context loss propagation
```

**A1** cobre somente raw-source admission, ambiguity/duplicate keys e fixtures.
**A2** possui somente compatibilidade + projeção raw→normalized: resolve selector legado
por comparação lowercase (id==token, description==token, token substring id ou
description), preserva todos os matches, projeta aliases de regra para
`ContractApplicability`, projeta `contract_bindings.get(pack_id, [])` em `contract_refs`
e produz `SelectionResolution {requested_token,resolved_pack_ids,status}`. **A3** possui
somente avaliação normalizada: consome outputs A2, avalia exact paths, operador legado
de patterns (final `*` prefix; demais substring), global, ApplicablePackSet,
ApplicableContractSet, must_hold scope/pack e consequências como
`SELECTED_PACK_MISSING`; A3 nunca reexecuta fuzzy/raw matching. O handoff A2→A3 é a
fronteira causal de ownership. **A4** usa a autoridade v1 `canonical_json/canonical_len`
ou prova substituição equivalente, qualifica optional-minimal context único, recompõe
applicability/kernel/custo por candidate e preserva/substitui explicitamente FFD/tie-break.
**A5** prova que perda requerida/source/hunk chega ao gate.

Gate B continua sendo AgentEscala#869 (target projection/config). Gate C é a
conformance do **par exato** engine SHA + target/config SHA. Depois de A+B+C, o
**Control B** de utilidade semântica (#805) é executado sob grant explícito de
provider real, com owner `#221 V1-C2 semantic-utility evaluation`, expectativas
pré-declaradas e anti-cherry-pick. Os dois braços e todas as tentativas admitidas usam
uma única configuração de inferência pré-declarada (endpoint Router, preset,
provider/model resolvidos, instrução/método, opções de request/geração e retry policy),
registrada por `inference_config_id`; cada braço registra request identity/digest e
somente o subject pode diferir. Para sampling: se seed determinístico for suportado pela
rota/modelo real, usa-se o mesmo seed por par; senão o grant congela antes do primeiro
run `pair_count` + `pass_criterion` de um repeated-pair protocol, sem adaptive stopping,
descarte ou rerun até favorável. Sampling mode/seed/pair index são registrados. Drift de
configuração ou protocolo invalida o controle. O pass criterion mínimo de Control B é direcional: o bad arm deve produzir o material adverse signal pré-declarado e o corrected arm não deve reproduzi-lo; em repeated-pair, K/N são congelados antes do run. Somente A+B+C + Control B podem conceder
`V1_C2_CONTRACT_CONTEXT_AND_LIMITATION_READY`.

Os terminais `V1_C2_CONTRACT_INTERFACE_DECIDED` e
`V1_C2_NORMALIZED_SEMANTIC_INTERFACE_DECIDED` são históricos supersedidos.


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
- AgentEscala#871 (HTTP 429 sem retry / sem `Retry-After`) é bugfix target-side limitado e **pré-requisito externo do freeze final**, fora da reconstrução de claims da V1-C6; falha persistente continua virando revisão manual.
- Non-features do v1: `json_schema` no provider, reparo/retry semântico de saída malformada, nova estratégia de modelo/preset, nova arquitetura de agrupamento semântico, receipts/trust architecture v2, OGR/impact-context.
- PR grande/evidência pesada: `chunk_budget_exceeded`/plano parcial não é blocker automático quando falha fechado para revisão manual. Exigência é **veracidade**, não escalabilidade.

**Gate terminal de freeze registrado no checkpoint de #221:** #232/#343/#307/#315 com disposições terminais e evidência exact-subject; par de contexto da C2 qualificado; AgentEscala#871 fecha por um único exit: FIXED com evidência qualificada, ou RISK_ACCEPTED sob grant humano explícito dedicado com exact unfixed SHA + residual/fail-closed/support evidence; release final imutável e reproduzível; AgentEscala consome exatamente esse par engine+config em canário controlado; 429/504/schema-inválido/budget-excedido permanecem fail-closed sem fabricar cobertura/readiness; rollback/suporte/limitações documentados.

## Pré-requisitos externos do freeze final

`V1_FINAL_SOURCE_CANDIDATE_READY != V1_FINAL_FREEZE`.

A V1-C6 reconstrói somente claims/obrigações/countermodels canônicos do AgentReview.
Os seguintes itens são **externos à reconstrução de claims C6**, mas bloqueiam o
freeze final:

- **AgentEscala#869:** target contract/projection/config e conformance do par exato;
- **AgentEscala#871:** exatamente um exit explícito fecha o prerequisite:
  **FIXED** (exact target commit mergeado/qualificado + testes verdes + retry bounded/
  `Retry-After` + falha persistente manual) **OU RISK_ACCEPTED** (grant humano explícito
  dedicado, exact unfixed SHA, evidência operacional fail-closed, residual nomeado de
  perda de cobertura por 429 sem retry/Retry-After e release/support/rollback docs). Silêncio
  ou issue apenas aberta não são aceitação; RISK_ACCEPTED não afirma reliability corrigida;
- release final de manutenção imutável/reproduzível;
- repin do consumer para o par compatível exato;
- canário controlado;
- prova de rollback + support/limitation documentation.

Para #871 a evidência é **exit-specific**: FIXED exige issue/repo identity + exact
target commit mergeado/qualificado + acceptance tests verdes + retry bounded/
Retry-After + falha persistente manual; RISK_ACCEPTED exige grant humano explícito
+ exact unfixed SHA + evidência operacional fail-closed + residual nomeado +
release/support/rollback docs. Nenhum artefato exclusivo de FIXED é exigido no exit
RISK_ACCEPTED. Não se cria claim semântica do engine para um bug de transporte do wrapper.


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
