# NON_NORMATIVE_CAMPAIGN_ARTIFACT
# Status: V1_C0_CONTRACT_CANDIDATE
# AgentReview v1 — Countermodel pack

Subject de derivação: `ab92e89f096b391bc50759afa3d0f6050881633a`. Referências de
linha relativas a esse commit.

Classes de evidência usadas:

| Classe | Significado |
|---|---|
| `REPRODUCED` | executado nesta slice pelo caminho real indicado; comando e saída abaixo |
| `WITNESSED_IN_COMMITTED_TEST` | um teste commitado no subject já fixa o comportamento |
| `OBSERVED_CODE` | derivado por leitura do código no subject; ainda sem execução |
| `HISTORICAL` | registrado em issue/PR datada; não é observação atual |

Todo countermodel sem `REPRODUCED` precisa de RED na sua slice antes de qualquer patch.

## Rescope AOCM (PR #367, 2026-10-01)

`RawSerializationGrammarTotality != NormalizedSemanticInterfaceTotality`. A tabela
de formas YAML abaixo é preservada como observação e insumo para o parser sucessor,
mas está `SUPERSEDED_AS_TOTAL_RAW_GRAMMAR`: não é mais prova normativa de que toda
serialização foi decidida nesta PR. O que fecha aqui é a semântica após admissão:
ambiguidade ou forma não suportada falha fechada antes da normalização; uma fonte
admitida tem exatamente uma projeção normalizada; e duas implementações conformes não
podem divergir em identidade, seção, relação, aplicabilidade, kernel ou gate.

---

## CL-1 — Coverage truth (#232)

### CM-CL1-01 — histórico: arquivo não-`must_review` sem hunk textual declarado coberto
- **Entrada:** arquivo `should_review`/`may_summarize` cujo bloco de diff não tem cabeçalho `@@` (binário, `Binary files … differ`).
- **Caminho:** `semantic_chunker.py:208-220` exclui só `required_files`; o arquivo é empacotado; chunk `coverage="complete"` (`:426`); `files_covered` inclui o arquivo; `chunk_payload_builder.py:180` emite `chunk_diff_hunk_missing:<p>` **apenas** em payload/manifest, que não são entrada de parse/synthesize/gate.
- **Resultado falso:** plan `complete`; gate `passed`; arquivo contado como coberto.
- **Evidência:** `WITNESSED_IN_COMMITTED_TEST` — `tests/agent_review/test_agent_review_e2e_contract.py` (fixture `agentescala_e2e/artifacts/full.diff` sem hunk para `calendar_page.jsx`, `tests/test_shift_service.py`, `.github/workflows/agent-review.yml`) afirma `status == "passed"`, `approve_with_minor_notes`, `manual_review_required is False` (`:925-927`). O teste fixa o defeito; a slice V1-C1 deve reconciliá-lo, não mantê-lo verde por construção.
- **Discriminador exigido:** mesmo PR com e sem o hunk textual do arquivo não-must; somente a variante sem hunk deve sair de `files_covered` e tornar a cobertura visível como não completa no gate.

### CM-CL1-02 — mode-only / rename 100% / arquivo novo vazio
- Mesma classe de CM-CL1-01; `block_has_observable_textual_hunk` (`payload_cost_model.py:199-214`) já os classifica como sem hunk. `OBSERVED_CODE`.

### CM-CL1-03 — artefato `full-diff` ausente
- `diff_by_file` retorna `{}` (`payload_cost_model.py:171-173`); todo arquivo é sem hunk; arquivos must são excluídos, os demais são empacotados como `complete`. `OBSERVED_CODE`.

### Anti-countermodel (não quebrar): fixtures sem diff
- 12 testes de `test_semantic_chunker.py` (fixture `_intake()` sem `full-diff`), `test_aiops_review_plan_chunks_cli.py::test_plan_chunks_cli_generates_semantic_chunk_plan` e o e2e acima quebram se a exclusão for generalizada sem ajustar fixtures (tentativa revertida em #231). Esses fixtures devem receber hunks sintéticos onde o teste **não** é sobre disponibilidade de hunk; `"não testamos hunks" != "hunks nunca importam"`.
- Acoplamento: a projeção do planner fixa `"declared_coverage": "complete"`/`"chunk_plan_limitations": []` (`payload_cost_model.py:1488,1492`); qualquer mudança em `chunk.limitations` precisa ser espelhada ou o guard do builder dispara.

**Mecanismo atual (não o histórico):** desde `d3f5946c4d0513def9f7c2018b63703a53df1cc7`, disponibilidade de hunk vale para toda tier: arquivo sem hunk sai de packing e de `files_covered`, entra em `files_not_covered`; `must_review` recebe `must_review_hunk_unavailable:<path>`, os demais compõem `hunk_unavailable_count:<N>`, e o plano fica degraded/non-complete até o gate. `supported_on_master_unreleased` não afirma adoção pelo consumer pinado.

---

## CL-2 — Consumo de contexto e honestidade de limitação; exigência per-claim #805 (#221 / AgentEscala#869)

### CM-CL2-01 — AgentEscala #805 (testemunha histórica)
- **Subject histórico:** `mglpsw/AgentEscala#805`, base `d9f78e58…`, head `b85717420749b8ad04d71b75b84299c5894f01c9`, run v1 `35152357843`, veredito `approve_with_required_followup`. `HISTORICAL` (comentário de #221, 2026-09-16).
- **Essência a preservar sem depender do target vivo:** `VisualGroup` DIA+NOITE qualificado como `composed_24h_same_assignee`; `_project_swap_targets_day()` emite apenas targets `single_shift` com `companion_shift_id=None`; a claim declarada exigia fidelidade de par (`logical_24h_pair`). Teste do próprio PR fixava o comportamento incorreto.
- **Resultado falso:** cobertura `7/0/0`; P2 para rename de teste; P3 para `List[dict[str, Any]]`; claim falsificada não reportada.
- **Por que v1 não pode detectar hoje (OBSERVED_CODE):** o corpo do PR nunca entra no payload; nenhum `must_hold` do pack `operational_slots_24h` do target cobre fidelidade de par; nenhum slot de resposta por claim existe; nenhum consumidor downstream consome estado de claim.

- **Disposição (decisão do owner, 2026-09-30):** per-claim coverage estruturada é non-claim do v1 final (adiada para #353/#357). Este countermodel permanece como **controle A** (regressão determinística: pack pertinente → contrato de domínio/must-hold pertinente no payload → não removido silenciosamente por orçamento → finding material ligado ao contrato preservado por parser/synth/gate) e **controle B** (avaliação semântica limitada, subject defeituoso vs corrigido, expectativas pré-definidas). Ver `02` OBL-CL2-07.

### CM-CL2-02 — contratos do target descartados como "irrelevantes"
- `_flatten_contract_rules` (`payload_cost_model.py:789-811`) exige `rules:` top-level em lista; `_flatten_review_packs` (`:843-858`) exige `packs:` em lista. O `.aiops/domain-contracts.yaml` da AgentEscala usa regras aninhadas sem `id`; `.aiops/review-packs.yaml` usa `packs:` como mapping.
- **Identidade imutável da observação:** `mglpsw/AgentEscala@e9cc03ff76a383b34f2871e59542f1789ce9012b`, tip de `develop` de 2026-09-30T04:19:39Z até 18:44:59Z (quando `develop` avançou para `1d773507`); essa janela cobre todas as leituras desta campanha, que portanto são recuperáveis nesse commit. Os mesmos dois blobs estão presentes no head de #805 (`b85717420749…`), isto é, o run de #805 viu estes documentos. `.aiops/domain-contracts.yaml` blob `e0ca56844cceaba1afac325856e305ca1342257e` (chaves top-level, exatamente 11: `version, updated, system, calendar, swaps, coverage_export, audit, notifications, security, auth_admin, response_model_rules`; sem `rules:`); `.aiops/review-packs.yaml` blob `16ae9a5d1d494f1128f3ed9b50a80d84e5797eec` (`packs:` é mapping: `calendar, operational_slots, admin_scale, swaps, coverage_export`). A slice V1-C2 reproduz por `git show e9cc03ff:<path>`/blob SHA, não pelo `develop` vivo.
- **Resultado falso:** ambos achatam para `[]`; todo chunk recebe contexto de contrato vazio e a limitação `contracts_context_not_relevant:<chunk>` — rótulo que afirma irrelevância quando a causa é forma não suportada.
- **Evidência (dividida por passo):**
  - achatamento para `[]`: `REPRODUCED` em 2026-09-30 por `evidence/cm_cl2_02_repro.py`, que verifica a identidade git-blob de cada entrada antes de chamar `_flatten_contract_rules`/`_flatten_review_packs` reais. Saída: `domain-contracts blob=e0ca5684… top_level_rules=NoneType flattened=0` e `review-packs blob=16ae9a5d… packs_type=dict flattened=0`. Entradas obtidas por `gh api repos/mglpsw/AgentEscala/git/blobs/<sha>`;
  - emissão de `contracts_context_not_relevant` a partir de listas vazias: `OBSERVED_CODE` (`payload_cost_model.py:435-436`), não executada;
  - relação com o output publicado de #805: `HISTORICAL`.
- *Histórico superado:* a versão anterior deste item dizia "formas lidas no `develop` vivo; efeito inferido". Mantida aqui só como registro; a qualificação acima não herda dela.
- **Discriminador exigido:** documento de contrato não vazio e não achatável deve produzir limitação distinta de "não relevante".

### CM-CL2-03 — fonte conhecida ausente rotulada como "não relevante"
- **Entrada:** um dos slots conhecidos está ausente; requiredness é calculada separadamente por `RequiredForChunk`, nunca por um campo novo de perfil.
  - **A.** `.aiops/domain-contracts.yaml` ausente.
  - **B.** `.aiops/review-packs.yaml` ausente.
- **Caminho atual (`OBSERVED_CODE`):** `repo_profile._load_optional_yaml` retorna `None` sem limitação quando o arquivo não existe (`app/agent_review/repo_profile.py:95-102`); `contracts_context` então achata essa fonte para vazio; quando a outra fonte também não produz item aplicável, emite `contracts_context_not_relevant:<chunk>` (`payload_cost_model.py:435-436`); quando a outra fonte produz item, a ausência fica silenciosa. Os fixtures RED neutralizam a outra fonte. Evidência: `OBSERVED_CODE` (exige RED antes do patch).
- **Resultado falso (ambas as variantes):** ausência indistinguível de não aplicabilidade; ou o implementador degrada toda ausência, destruindo o controle positivo.
- **Discriminador exigido:** A e B produzem a classe semântica `SOURCE_ABSENT`, com `source_kind` respectivamente `domain_contracts` e `review_packs`; nunca `not_relevant`. A forma serializada exata é autoridade do Gate A sucessor, não desta campanha. Par focal A/B: ausência + `RequiredForChunk=true` é degradante e não conclusiva; o mesmo slot ausente + `RequiredForChunk=false` permanece tipado, mas o controle positivo pode ser conclusivo. Não se cria `TargetProfile` field (OBL-CL2-03, família 14).
- **Origem:** achado pós-Ready do Codex 4150077445 na PR #367, adjudicado válido pelo owner.

### Gate A sucessor — RawSourceAdmissionContract

Antes de qualquer patch runtime, o sucessor congela e testa o contrato de admissão
bruta para: os blobs atuais AgentEscala de domain-contracts e review-packs; ambos os
fixtures legados commitados; controle negativo de chave duplicada; raiz malformada;
e packs/bindings malformados. O contrato exige:

```text
fixture admitido -> exatamente uma NormalizedProjection
fixture rejeitado -> um resultado tipado invalid/unsupported
                  -> nunca empty/not_relevant silencioso
```

Chaves duplicadas são o controle obrigatório da classe `ambiguous raw serialization`:
ela é inválida antes da normalização, não um novo campo semântico. A tarefa sucessora
escolhe uma única forma serializada determinística para cada classe de reason code;
esta campanha só congela as classes `SOURCE_ABSENT`,
`SOURCE_INVALID_OR_UNSUPPORTED`, `SELECTED_PACK_MISSING`, `SELECTED_CONTRACT_MISSING`,
`OPTIONAL_CONTEXT_REDUCED`, `REQUIRED_CONTEXT_OMITTED` e `MUST_HOLD_OMITTED`.

O controle positivo normalizado é: um pack aplicável seleciona exatamente sua relação
de contrato; um pack sem relação não injeta contrato espúrio. A ausência de relação,
referência explícita ausente e fonte inválida permanecem semanticamente distintos.

### Histórico P1–P9 — requisitos preservados; autoridade executável movida ao Gate A

| Id | Mecanismo e contramodelo focal | Discriminador | Controle positivo |
|---|---|---|---|
| P1 / CM-NORM-01 | schema finito de `NormalizedContract` | scalar admitido sem campo declarado é rejeitado, nunca posto em mapa residual | scalar declarado vira `SemanticValue` ordenado |
| P2 / CM-NORM-02 | união `TextItem`/`RuleItem` como requisitos de conteúdo | nenhum campo semântico requerido pode desaparecer ou trocar de papel; ordem de itens-fonte é requisito | lista de textos/regras mantém a sequência-fonte; forma executável/canônica é qualificada no Gate A |
| P3 / CM-NORM-03 | papéis finitos raw→`RuleItem` + kernel mecânico | campo de rule-object sem papel declarado é rejeitado no Gate A; perda de campo projetado requerido gera `REQUIRED_CONTEXT_OMITTED` | `{id,description}` legado e campos `rule` declarados projetam nos papéis especificados; perda apenas de rationale gera `OPTIONAL_CONTEXT_REDUCED` |
| P4 / CM-NORM-04 | schema e censo finitos de `NormalizedPack` | metadata fora dos campos declarados não vira contexto normalizado silencioso | description/preset declarados são projetados |
| P5 / CM-PACK-01 | observabilidade separada de pack e contrato | qualquer pack aplicável mantém `pack_id` como contexto requerido, mesmo sem relação/description/preset; portanto não é `not_relevant` | ref resolvida produz evidência de pack e contrato; pack-only mantém identity |
| P6 / CM-PACK-02 | predicado de `not_relevant` | somente `ApplicablePackSet == empty` E `ApplicableContractSet == empty` após avaliação válida permitem `not_relevant` | zero packs + zero contratos produz o único controle positivo de irrelevância |
| P7 / CM-ABS-01 | classes semânticas separadas da serialização | nova grafia futura não muda a classe `SOURCE_ABSENT` nem consequência | slot ausente fica tipado sem inventar irrelevância |
| P8 / CM-HUNK-01 e CM-HUNK-02 | piso de hunk completo + kernel | hunk truncado, ou hunk completo sem kernel, é não conclusivo | hunk integral com kernel integral pode ser conclusivo |
| P9 / CM-GA-A4-COST + CM-GA-A4-REPACK | owner boundary para determinismo executável | custo divergente ou repack/tie-break divergente permanece RED até Gate A A4 | A4 congela uma única autoridade de custo/packing e mantém o positive control de redução só opcional |

`CM-HUNK-01` é um arquivo cuja única evidência é contexto completo, mas cujo hunk
fica abaixo do piso; `CM-HUNK-02` mantém hunk integral, porém perde contexto requerido.
Ambos provam que nem presença de hunk nem contexto isoladamente satisfazem o piso.

Controles positivos obrigatórios do corpus: contrato real `calendar`; lista de topo
`response_model_rules`; seção mista de `auth_admin`; pack aplicável com
`domain_contract`; pack aplicável sem relação de contrato (o `pack_id` sozinho já é contexto requerido);
pack verdadeiramente irrelevante apenas quando nenhum pack e nenhum contrato se aplicam; fixture legado A `tests/agent_review/fixtures/agentescala_e2e`; e
fixture legado B `tests/agent_review/fixtures/agentescala_minimal`. Eles devem demonstrar que o
fechamento finito não torna toda revisão não conclusiva.

---

## CL-3 — Materiality (#343)

Todos `HISTORICAL` (AgentEscala #802/#804 via #343). Cada um deve virar resposta de modelo gravada, reproduzida por parse → synthesize → gate.

| Id | Countermodel | Por que não é finding material |
|---|---|---|
| CM-CL3-01 | símbolo citado ("sem docstring") inexistente no repositório | aboutness falsa: sem objeto, sem consequência |
| CM-CL3-02 | símbolo real atribuído a módulo errado | aboutness falsa |
| CM-CL3-03 | "setting sem documentação" contradito pela árvore (documentado em 4 lugares) | afirmação de ausência falsa; evidência citada era a linha do default |
| CM-CL3-04 | rename/path sem consumidor quebrado demonstrado | sem consequência negativa |
| CM-CL3-05 | alteração YAML apenas de formatação / pin imutável de action descrito como regressão | sem relação causal |
| CM-CL3-06 | pedido especulativo de testes (inclusive "testes para arquivo Markdown") | sem consequência; especulativo |

- **Restrição de canal (OBSERVED_CODE):** o v1 não tem canal neutro de "observação". `_downgrade_or_reject` (`finding_normalizer.py:205-227`) produz `NormalizedRisk(source="downgraded_finding")`, e qualquer risk leva a `approve_with_required_followup` (`final_synthesizer.py:474-475`; `quality_gate.py:544-545`); `rejected_findings` leva a `approve_with_minor_notes` (`final_synthesizer.py:476`). Rebaixar um P3 para risk torna o veredito **mais** estrito. O canal-alvo é decisão aberta da slice V1-C3; o discriminador deve observar a partição confirmed/risk/rejected, não só `normalized_verdict`.
- **Estado atual (OBSERVED_CODE):** `finding_normalizer.py:122-202` só verifica presença de campos, termos especulativos (`SPECULATIVE_TERMS`), evidência placeholder e fonte de falha de teste. `impact` é texto livre nunca avaliado; P2/P3 nunca são revalidados pelo gate.
- **PC-CL3-01 (positive control obrigatório):** violação real de contrato + evidência concreta no hunk + consequência demonstrada → deve sobreviver como confirmada na mesma severidade. Proibido fechar falso positivo eliminando sensibilidade a defeito real.

---

## CL-4 — Limitation propagation

| Id | Limitação | Destino observado |
|---|---|---|
| CM-CL4-01 | `chunk_diff_hunk_missing:<p>` (não-must) | descartada antes do gate (sobreposição com CM-CL1-01) |
| CM-CL4-02 | `contracts_context_not_relevant:<id>` (em `limitations` do payload); `auxiliary_context_reduced`, `checks_context_reduced`, `evidence_context_reduced`, `contracts_context_reduced` (em `truncation.coverage_impact` do payload, `chunk_payload_builder.py:503-507`); `coverage_validation_evidence_reduced`/`coverage_checks_reduced` (brief, `pr_brief.py:316-317`) | descartadas antes do gate |
| CM-CL4-03 | `aux_context` (inclui `must_hold`) `omitted_due_to_budget` | descartada antes do gate; `must_hold` some silenciosamente |

`OBSERVED_CODE`: manifest e brief não são entrada de `aiops-review-{parse-chunks,synthesize,quality-gate}.py`. Apenas limitações de nível plan alcançam o gate (via `plan.status`/`files_not_covered`). Nem toda limitação deve bloquear: a slice que tocar cada classe deve classificá-la como informativa ou degradante-de-obrigação, com teste por classe.

---

## CL-5 — Non-vacuous blocker / result coverage (#307)

### CM-CL5-01 — P1 em arquivo que o próprio chunk declarou não revisado
- **Evidência:** `REPRODUCED` (ver comando abaixo): `parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]` — idêntico ao positive control (P1 em arquivo revisado).
- **Causa:** `_has_parsed_source_chunk` (`quality_gate.py:411-416`) exige apenas chunk parseado; `finding_normalizer.py:138` exige apenas `file_path ∈ chunk.files`; nenhum consumidor cruza finding × estado de cobertura do arquivo.
- **Consumidor afetado:** matriz do publisher (`docs/AGENT_REVIEW_QUALITY_GATE.md:103-125`) trata como "conclusive blocking publication".

### CM-CL5-02 — modelo declara não ter revisado nada; gate aprova
- **Evidência:** `REPRODUCED`: todos os arquivos em `files_not_reviewed`, sem findings, PR não crítica → `parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]`, com e sem plan passado a synthesize/gate. Com `critical_pr=True` → `manual_review_required` (`critical_all_chunk_coverage_not_reviewed`).
- **Direção do erro:** falso-aprovador. É a forma não vacuosa da asserção "no-plan all-not-reviewed" de #307.
- **Causa:** `final_synthesizer.py:362-366` conta `files_not_reviewed` como "reportado" (não gera `coverage_expected_files_missing`); `_result_status` (`chunk_result_parser.py:194-211`) ignora cobertura; `_critical_coverage_gaps` só roda com `critical_pr`.

### CM-CL5-03 — TS1 literal
- `HISTORICAL` + `OBSERVED_CODE`: TS1 é mutante do trust set `_RESULT_IDENTITY_TRUST_LIMITATIONS` da PR #275 (fechada sem merge; branch `fix/agentreview-v1-u2-result-coverage-truth` @ `2b5a2726`). Esse mecanismo está **ausente** em master. Via API Python (modelos pydantic mutáveis), `ChunkResults` com `schema_id`/`target_repo` adulterados após construção + P1 válido → gate `passed/changes_requested` sem warning. Via CLI, `load_chunk_results` rejeita schema divergente (`quality_gate.py:151-158`).
- **Obrigação de fechamento (missão 2 de #307):** OBL-CL5-04 — P1 em `ChunkResults` com identidade divergente do subject/plan é blocker não confiável.
- **Reprodução exigida de TS1 (missão 1 de #307):** esta é a forma literal, via API Python. CM-CL5-01/02 são countermodels adicionais de result coverage no escopo de #307, **não** TS1.
- A asserção vacuosa citada em #307 existe **somente** na branch de #275 (`test_quality_gate.py:1553-1618` naquela head); ela passa por `final_review_mutated` e não pela lógica de cobertura. Não portar.

**Reprodução (read-only):**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B campaign/agent-review-v1-freeze/evidence/cm_c5_repro.py .
```

Saída no subject de derivação:

```text
CM-CL5-02 all_not_reviewed noncritical: parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]
CM-CL5-02 all_not_reviewed noncritical no-plan (synth+gate without plan): parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]
CM-CL5-02 all_not_reviewed critical: parse=complete synth=complete/approved gate=manual_review_required/manual_review_required/manual=True lim=['critical_all_chunk_coverage_not_reviewed']
CM-CL5-01 P1 on not_reviewed file: parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]
PC-CL5 P1 on reviewed file (positive control): parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]
PC-CL5 clean all reviewed: parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]
```

---

## CL-6 — Egress (#315)

| Id | Lane | Egress observado | Proteção |
|---|---|---|---|
| CM-CL6-01 | L-CHUNK (configurada no consumer CT104, ver 00 §2; wrapper observado em `AgentEscala@e9cc03ff:.github/workflows/agent-review.yml`, blob `7fff440c400e7294b4c333a4f45d5757957082b4`, envelope `:592-620`) | payload inteiro: bloco `git diff` cru por arquivo (`payload_cost_model.py:170-196` → `chunk_payload_builder.py:169-178`) + paths relativos | regex secrets + redaction de path absoluto/home |
| CM-CL6-02a | L-LEGACY `/agent review llm` (`_build_sanitized_bundle`, `github_agent_review.py:1415-1589`, chamado em `:2044`/`:2795`) | título/corpo do PR (`:1441-1442`), paths crus, patch completo só para PR com ≤3 arquivos e ≤30 000 chars de diff, ≤2 000 chars por arquivo / 6 000 no total de source head para certos arquivos frontend (`_fetch_file_at_ref`, `:1489`, `:2001`); bundle inteiro truncado a `MAX_LLM_BUNDLE_CHARS=32 000` (`:29`, `:1659`) — um diff de 30 000 chars não tem transporte integral garantido após metadados | regex inline de segredos (`:43-54,438-450`), **sem** redaction de path; não usa `app/agent_review/redaction.py` |
| CM-CL6-02b | L-LEGACY `/agent ask` (`_build_ask_bundle`, `:1687-1721`, chamado em `:2118`) | pergunta, título/corpo, paths crus + trechos de patch de 180 chars; **sem** fetch de arquivo; truncado a `MAX_BUNDLE_CHARS=6 000` (`:28`) | mesma regex inline |
| CM-CL6-03 | L-CHUNK (a L-LEGACY não usa esse campo) | `output_safe_for_llm=True` constante (`cli.py:109`), checado como se fosse computado (`aiops-review-build-payloads.py:362`) | nenhuma |

`OBSERVED_CODE`. Implicação para a disposição: um revisor de código precisa transmitir o código revisado; uma garantia estrutural "nenhum source cru" é incompatível com a função da L-CHUNK. As opções de #315 permanecem A/B/C; B (aceitar risco) e C (desabilitar lane = redução funcional) são reservadas a decisão humana por #221.

---

## CL-7 — Publication fidelity

Sem countermodel ativo no engine. Evidência `HISTORICAL` (AgentEscala #803). Limitação: o digest é produzido e verificado sobre o mesmo artefato CT104 — prova não-alteração entre producer e publisher, não atestação independente do resultado semântico.


## Requirements freeze pós-review exact-head da PR #367

As revisões do exact head `ad9403f065a836298defdf51bf9e6122fea7cdd1`
demonstraram que a campanha não deve prometer que prosa determina bytes/chunks.
O terminal atual é `V1_C2_REQUIREMENTS_AND_INVARIANTS_FROZEN` e os seguintes
contraexemplos passam a ser **famílias obrigatórias do Gate A executável**:

- **CM-GA-A2-LEGACY-PACK:** o input legado seleciona `contract_pack=calendar` e o
  fixture commitado possui `pack_id=agentescala-calendar`. Gate A A2 deve preservar
  a compatibilidade atual e entregar ao estágio normalizado um pack identity exato;
  fuzzy matching não vira semântica do mapping mode.
- **CM-GA-A2-LEGACY-APPLICABILITY:** os aliases legados `scope`, `is_global`,
  `file_path`, `path`, `files`, `paths`, `source_files`, `related_files` e
  `patterns` não podem desaparecer. A2/A3 deve projetá-los em um único conceito
  finito `ContractApplicability` preservando exact-path OR pattern OR global.
- **CM-GA-A4-COST:** duas representações de custo não podem decidir packing de forma
  diferente. A4 deve usar a autoridade v1 `canonical_json/canonical_len` ou provar
  uma substituição equivalente no domínio suportado; a unidade atual é comprimento
  do texto JSON canônico Python, não "bytes" genéricos.
- **CM-GA-A4-REPACK:** contexto opcional não possui ordem arbitrária de remoção. A4
  qualifica um único optional-minimal context, recompõe applicability/kernel/custo
  para cada candidate file set e preserva ou substitui explicitamente o FFD/tie-break
  existente. O controle focal de pack remove somente `review_preset`: deve emitir
  `OPTIONAL_CONTEXT_REDUCED`, preservar `pack_id` como contexto requerido e manter a
  possibilidade de conclusão limpa. Singleton oversize permanece não coberto.

Essas famílias não são consideradas mortas por esta PR documental. Elas ficam
**ROUTED_TO_EXECUTABLE_GATE_A** e só fecham quando código/testes do successor as
qualificarem. O positive control permanece: contexto requerido + hunks intactos que
cabem no budget continuam capazes de revisão conclusiva.

```text
RequirementsFrozen != ExecutableContractQualified
```



### CM-GA-A2-LEGACY-SELECTOR-SURFACE

- **Owner:** Gate A A2.
- **Semântica retida:** token selecionado, `pack_id` e `description` são comparados em lowercase. Um pack legado resolve quando: id == token; description == token; token é substring de id; ou token é substring de description. Todos os packs que satisfazem a regra entram em `resolved_pack_ids`; A2 não escolhe arbitrariamente um único match.
- **Controles positivos:** igualdade case-insensitive por id; igualdade case-insensitive por description; substring de id (`calendar -> agentescala-calendar`, `aiops -> agentescala-aiops`); substring de description; múltiplos matches retornam o conjunto exato completo.
- **Non-claim:** MAPPING_PACK_MODE continua exact-id + `fnmatchcase` paths; fuzzy/substring é somente compatibility adapter legado.

### CM-GA-A3-LEGACY-SEMANTIC-GROUP

- **Owner:** Gate A A3.
- **Escopo:** somente itens normalizados oriundos de LEGACY_FLAT_MODE/LEGACY_PACK_MODE. Mapping/domain mode não ganha applicability por keyword quando relações explícitas existem.
- **Operador retido:** `_relevance_keywords(semantic_group)` com os grupos: `primary_backend_logic={backend,service,domain,api}`; `api_schema_contract={schema,contract,api,model}`; `frontend_ui={frontend,ui,component}`; `tests={test,coverage,assert}`; `workflow_aiops={workflow,aiops,pipeline}`; `docs_changelog={docs,changelog,readme}`; `suspicious_out_of_scope={secret,prod,deploy,runtime}`. Qualquer keyword como substring de `(id + ' ' + description).lower()` torna o item aplicável por este fallback.
- **RED:** legacy `backend-review` em `primary_backend_logic` deixa de entrar no contexto quando não há path/selector explícito.
- **GREEN:** keyword match legado aplica; legacy sem keyword nem outra relação não aplica; mapping-mode sem exact/path relation não é promovido por keyword.

### CM-GA-A3-LEGACY-PATTERN

- **Owner:** Gate A A3, consumindo patterns já normalizados por A2.
- **Operador retido:** trim; vazio ignora; pattern terminado em `*` faz prefix match removendo apenas o `*` final; qualquer outro pattern faz substring match sobre o canonical chunk path. Não é `fnmatchcase`.
- **Controles:** `calendar` casa path que contém `calendar`; `backend/api/*` casa por prefixo `backend/api/`; pattern sem match permanece não aplicável; nenhum glob adicional é inferido.

### CM-PACK-03 — explicit selected pack unresolved

- **Entrada:** `selected_contract_pack` não vazio, mas nenhum pack admitido resolve por id exato ou pela compatibilidade legada qualificada no Gate A A2.
- **Resultado proibido:** `ApplicablePackSet=[]` + `ApplicableContractSet=[]` sendo reinterpretados como `not_relevant`/conclusivo.
- **Discriminador:** classe semântica `SELECTED_PACK_MISSING`; applicability fica não resolvida, o review afetado é degradado/não conclusivo e `not_relevant` é proibido.
- **Controle positivo:** seleção exata válida resolve; no fixture legado, `calendar` resolve compativelmente para `agentescala-calendar`.
- **Owner executável:** A2 resolve o token raw para `SelectionResolution {requested_token,resolved_pack_ids,status}`; A3 consome somente esse resultado e aplica a consequência `SELECTED_PACK_MISSING` quando `status=unresolved`. A3 nunca reexecuta fuzzy/substring matching.

### CM-MUST-HOLD-01 — semantic-context applicability

- **A2:** normaliza `semantic-context.scope`, `change_type`, `contract_pack` e `must_hold`; resolve `contract_pack` pela mesma autoridade de seleção legada/exata e não inventa ids por must-hold.
- **A3:** `MustHoldApplicable(chunk)` usa scope + constraint de pack. Scope vazio ou `global|all|document` aplica globalmente; caso contrário lower/trim e tokens alfanuméricos casam por substring contra canonical chunk paths. Se `contract_pack` existe, pelo menos um exact resolved pack id precisa estar em `ApplicablePackSet(chunk)`.
- **RED:** scope backend + calendar resolvido, mas chunk frontend-only ou sem calendar-applicable pack recebe must_hold indevidamente.
- **GREEN:** backend/calendar chunk recebe must_hold; frontend-only não recebe; `contract_pack` não resolvido gera `SELECTED_PACK_MISSING` e não é reinterpretado como irrelevante.
- **change_type:** apenas metadata advisory nesta V1; não filtra applicability.

### Control B — semantic utility evaluation (#805)

O **owner executável** é `#221 V1-C2 semantic-utility evaluation`; o grant de
provider real é precondição de autorização, não substituto de ownership. O controle
roda **depois de Gate A + Gate B + Gate C determinísticos e antes de conceder
`V1_C2_CONTRACT_CONTEXT_AND_LIMITATION_READY`**.

```text
bad #805-style subject + applicable context
vs
corrected counterpart + same applicable context
```

Expectativas são congeladas antes do run, toda tentativa admitida é registrada e não
há rerun até resposta favorável. **Ambos os braços usam a mesma configuração de inferência
pré-declarada**: endpoint do Agent Router, preset, provider/model resolvidos,
instrução/método, opções de geração/request e retry policy. Cada tentativa registra
`inference_config_id` compartilhado, endpoint/preset/provider/model/options e a
request identity/digest de cada braço; somente o subject revisado pode diferir.
Drift de configuração invalida Control B e retém o terminal. Para sampling estocástico,
antes do primeiro run congela-se `sampling_mode`: se seed determinístico for suportado
pela rota/modelo real, ambos os braços do par usam o mesmo seed e o registram; se não,
usa-se protocolo repeated-pair com `pair_count` e `pass_criterion` pré-declarados no
grant, sem adaptive stopping, descarte de pares ou rerun até resultado favorável. Toda
tentativa registra pair index/sampling identity. Falhar a expectativa pré-declarada
retém o terminal C2; passar prova apenas utilidade semântica limitada, nunca recall.

### PC-PACK-PRESET-OPTIONAL

Pack aplicável com `review_preset` mantém `pack_id` requerido. Remover somente
`review_preset` -> `OPTIONAL_CONTEXT_REDUCED`; applicability não muda e conclusão
limpa continua possível se todo o restante do piso estiver presente.

### PC-CHANGE-TYPE-OPTIONAL

`semantic-context.change_type` é metadata advisory opcional nesta V1 e não filtra
`MustHoldApplicable`. Com must_hold aplicável preservado, remover somente
`change_type` -> `OPTIONAL_CONTEXT_REDUCED`; a conclusão ainda pode permanecer limpa.

### CM-GA-A2-LEGACY-CONTRACT-IDENTITY

- **Owner:** Gate A A2.
- **Projection:** cada rule legacy admitida `{id,description,...}` gera exatamente um `NormalizedContract`: `contract_id=clean(id)`, uma seção `rules`, um `RuleItem.rule=clean(description)`. Campos de applicability vão para `ContractApplicability`, não para qualifiers.
- **Admissão:** `id` e `description` devem ser strings não vazias; ausência/malformação -> `SOURCE_INVALID_OR_UNSUPPORTED`.
- **RED:** agrupar múltiplas rules sob um synthetic contract, ou manter id apenas como qualifier, quebra `contract:<id>`.
- **GREEN:** `calendar_10_22_independent` e `no_auto_contract_fix` permanecem contratos distintos; `contract:calendar_10_22_independent` resolve exatamente um.

### CM-GA-A2-PATTERN-NORMALIZATION

- **Owner:** Gate A A2.
- `patterns` raw list: aceitar strings, trim, descartar empty-after-trim, aplicar sanitização de path compatível com v1, deduplicar exato e ordenar. A3 recebe somente patterns canônicos não vazios.
- **Control:** `['  backend/api/*  ', ' ', 'backend/api/*'] -> ['backend/api/*']`; A3 não trim/drop novamente.

### CM-GA-A2-SELECTOR-ALIASES

- **Owner:** Gate A A2.
- Token explícito é o primeiro valor clean/non-empty entre `contract_pack` e alias legado `pack`; `contract_pack` tem precedência. Clean = string + strip; vazio após strip é absent.
- **Controls:** `contract_pack: ' Calendar '` -> `Calendar`; contract_pack blank + `pack: ' Calendar '` -> `Calendar`; ambos presentes -> contract_pack vence; ambos vazios -> sem seleção explícita.

### CM-MUST-HOLD-02 — pack source required by semantic-context

Se `semantic-context.contract_pack` é não vazio, `review_packs` é `RequiredForChunk` antes da resolução. Ausência/invalidade do source degrada; não pode cair no branch `absent_when_not_required` nem em `not_relevant`.
