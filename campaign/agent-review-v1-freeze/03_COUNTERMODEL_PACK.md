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

---

## V1-C1 — Coverage truth (#232)

### CM-C1-01 — arquivo não-`must_review` sem hunk textual declarado coberto
- **Entrada:** arquivo `should_review`/`may_summarize` cujo bloco de diff não tem cabeçalho `@@` (binário, `Binary files … differ`).
- **Caminho:** `semantic_chunker.py:208-220` exclui só `required_files`; o arquivo é empacotado; chunk `coverage="complete"` (`:426`); `files_covered` inclui o arquivo; `chunk_payload_builder.py:180` emite `chunk_diff_hunk_missing:<p>` **apenas** em payload/manifest, que não são entrada de parse/synthesize/gate.
- **Resultado falso:** plan `complete`; gate `passed`; arquivo contado como coberto.
- **Evidência:** `WITNESSED_IN_COMMITTED_TEST` — `tests/agent_review/test_agent_review_e2e_contract.py` (fixture `agentescala_e2e/artifacts/full.diff` sem hunk para `calendar_page.jsx`, `tests/test_shift_service.py`, `.github/workflows/agent-review.yml`) afirma `status == "passed"`, `approve_with_minor_notes`, `manual_review_required is False` (`:925-927`). O teste fixa o defeito; a slice V1-C1 deve reconciliá-lo, não mantê-lo verde por construção.
- **Discriminador exigido:** mesmo PR com e sem o hunk textual do arquivo não-must; somente a variante sem hunk deve sair de `files_covered` e tornar a cobertura visível como não completa no gate.

### CM-C1-02 — mode-only / rename 100% / arquivo novo vazio
- Mesma classe de CM-C1-01; `block_has_observable_textual_hunk` (`payload_cost_model.py:199-214`) já os classifica como sem hunk. `OBSERVED_CODE`.

### CM-C1-03 — artefato `full-diff` ausente
- `diff_by_file` retorna `{}` (`payload_cost_model.py:171-173`); todo arquivo é sem hunk; arquivos must são excluídos, os demais são empacotados como `complete`. `OBSERVED_CODE`.

### Anti-countermodel (não quebrar): fixtures sem diff
- 12 testes de `test_semantic_chunker.py` (fixture `_intake()` sem `full-diff`), `test_aiops_review_plan_chunks_cli.py::test_plan_chunks_cli_generates_semantic_chunk_plan` e o e2e acima quebram se a exclusão for generalizada sem ajustar fixtures (tentativa revertida em #231). Esses fixtures devem receber hunks sintéticos onde o teste **não** é sobre disponibilidade de hunk; `"não testamos hunks" != "hunks nunca importam"`.
- Acoplamento: a projeção do planner fixa `"declared_coverage": "complete"`/`"chunk_plan_limitations": []` (`payload_cost_model.py:1488,1492`); qualquer mudança em `chunk.limitations` precisa ser espelhada ou o guard do builder dispara.

---

## V1-C2 — Semantic claim coverage (#221 / #805)

### CM-C2-01 — AgentEscala #805 (testemunha histórica)
- **Subject histórico:** `mglpsw/AgentEscala#805`, base `d9f78e58…`, head `b85717420749b8ad04d71b75b84299c5894f01c9`, run v1 `35152357843`, veredito `approve_with_required_followup`. `HISTORICAL` (comentário de #221, 2026-09-16).
- **Essência a preservar sem depender do target vivo:** `VisualGroup` DIA+NOITE qualificado como `composed_24h_same_assignee`; `_project_swap_targets_day()` emite apenas targets `single_shift` com `companion_shift_id=None`; a claim declarada exigia fidelidade de par (`logical_24h_pair`). Teste do próprio PR fixava o comportamento incorreto.
- **Resultado falso:** cobertura `7/0/0`; P2 para rename de teste; P3 para `List[dict[str, Any]]`; claim falsificada não reportada.
- **Por que v1 não pode detectar hoje (OBSERVED_CODE):** o corpo do PR nunca entra no payload; nenhum `must_hold` do pack `operational_slots_24h` do target cobre fidelidade de par; nenhum slot de resposta por claim existe; nenhum consumidor downstream consome estado de claim.

### CM-C2-02 — contratos do target descartados como "irrelevantes"
- `_flatten_contract_rules` (`payload_cost_model.py:789-811`) exige `rules:` top-level em lista; `_flatten_review_packs` (`:843-858`) exige `packs:` em lista. O `.aiops/domain-contracts.yaml` da AgentEscala usa regras aninhadas sem `id`; `.aiops/review-packs.yaml` usa `packs:` como mapping.
- **Resultado falso:** ambos achatam para `[]`; todo chunk recebe contexto de contrato vazio e a limitação `contracts_context_not_relevant:<chunk>` — rótulo que afirma irrelevância quando a causa é forma não suportada. `OBSERVED_CODE` (formas do target lidas no `develop` vivo; efeito inferido e consistente com o output de #805).
- **Discriminador exigido:** documento de contrato não vazio e não achatável deve produzir limitação distinta de "não relevante".

---

## V1-C3 — Materiality (#343)

Todos `HISTORICAL` (AgentEscala #802/#804 via #343). Cada um deve virar resposta de modelo gravada, reproduzida por parse → synthesize → gate.

| Id | Countermodel | Por que não é finding material |
|---|---|---|
| CM-C3-01 | símbolo citado ("sem docstring") inexistente no repositório | aboutness falsa: sem objeto, sem consequência |
| CM-C3-02 | símbolo real atribuído a módulo errado | aboutness falsa |
| CM-C3-03 | "setting sem documentação" contradito pela árvore (documentado em 4 lugares) | afirmação de ausência falsa; evidência citada era a linha do default |
| CM-C3-04 | rename/path sem consumidor quebrado demonstrado | sem consequência negativa |
| CM-C3-05 | alteração YAML apenas de formatação / pin imutável de action descrito como regressão | sem relação causal |
| CM-C3-06 | pedido especulativo de testes (inclusive "testes para arquivo Markdown") | sem consequência; especulativo |

- **Estado atual (OBSERVED_CODE):** `finding_normalizer.py:122-202` só verifica presença de campos, termos especulativos (`SPECULATIVE_TERMS`), evidência placeholder e fonte de falha de teste. `impact` é texto livre nunca avaliado; P2/P3 nunca são revalidados pelo gate.
- **PC-C3-01 (positive control obrigatório):** violação real de contrato + evidência concreta no hunk + consequência demonstrada → deve sobreviver como confirmada na mesma severidade. Proibido fechar falso positivo eliminando sensibilidade a defeito real.

---

## V1-C4 — Limitation propagation

| Id | Limitação | Destino observado |
|---|---|---|
| CM-C4-01 | `chunk_diff_hunk_missing:<p>` (não-must) | descartada antes do gate (sobreposição com CM-C1-01) |
| CM-C4-02 | `contracts_context_not_relevant`, `contracts_context_reduced`, `evidence_reduced`, `checks_reduced` | descartadas antes do gate |
| CM-C4-03 | `aux_context` (inclui `must_hold`) `omitted_due_to_budget` | descartada antes do gate; `must_hold` some silenciosamente |

`OBSERVED_CODE`: manifest e brief não são entrada de `aiops-review-{parse-chunks,synthesize,quality-gate}.py`. Apenas limitações de nível plan alcançam o gate (via `plan.status`/`files_not_covered`). Nem toda limitação deve bloquear: a slice que tocar cada classe deve classificá-la como informativa ou degradante-de-obrigação, com teste por classe.

---

## V1-C5 — Non-vacuous blocker / result coverage (#307)

### CM-C5-01 — P1 em arquivo que o próprio chunk declarou não revisado
- **Evidência:** `REPRODUCED` (ver comando abaixo): `parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]` — idêntico ao positive control (P1 em arquivo revisado).
- **Causa:** `_has_parsed_source_chunk` (`quality_gate.py:411-416`) exige apenas chunk parseado; `finding_normalizer.py:138` exige apenas `file_path ∈ chunk.files`; nenhum consumidor cruza finding × estado de cobertura do arquivo.
- **Consumidor afetado:** matriz do publisher (`docs/AGENT_REVIEW_QUALITY_GATE.md:103-125`) trata como "conclusive blocking publication".

### CM-C5-02 — modelo declara não ter revisado nada; gate aprova
- **Evidência:** `REPRODUCED`: todos os arquivos em `files_not_reviewed`, sem findings, PR não crítica → `parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]`. Com `critical_pr=True` → `manual_review_required` (`critical_all_chunk_coverage_not_reviewed`).
- **Direção do erro:** falso-aprovador. É a forma não vacuosa da asserção "no-plan all-not-reviewed" de #307.
- **Causa:** `final_synthesizer.py:362-366` conta `files_not_reviewed` como "reportado" (não gera `coverage_expected_files_missing`); `_result_status` (`chunk_result_parser.py:194-211`) ignora cobertura; `_critical_coverage_gaps` só roda com `critical_pr`.

### CM-C5-03 — TS1 literal
- `HISTORICAL` + `OBSERVED_CODE`: TS1 é mutante do trust set `_RESULT_IDENTITY_TRUST_LIMITATIONS` da PR #275 (fechada sem merge; branch `fix/agentreview-v1-u2-result-coverage-truth` @ `2b5a2726`). Esse mecanismo está **ausente** em master. Via API Python (modelos pydantic mutáveis), `ChunkResults` com `schema_id`/`target_repo` adulterados após construção + P1 válido → gate `passed/changes_requested` sem warning. Via CLI, `load_chunk_results` rejeita schema divergente (`quality_gate.py:151-158`).
- A asserção vacuosa citada em #307 existe **somente** na branch de #275 (`test_quality_gate.py:1553-1618` naquela head); ela passa por `final_review_mutated` e não pela lógica de cobertura. Não portar.

**Reprodução (read-only):**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B campaign/agent-review-v1-freeze/evidence/cm_c5_repro.py .
```

Saída no subject de derivação:

```text
CM-C5-02 all_not_reviewed noncritical: parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]
CM-C5-02 all_not_reviewed critical: parse=complete synth=complete/approved gate=manual_review_required/manual_review_required/manual=True lim=['critical_all_chunk_coverage_not_reviewed']
CM-C5-01 P1 on not_reviewed file: parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]
PC-C5 P1 on reviewed file (positive control): parse=complete synth=complete/changes_requested gate=passed/changes_requested/manual=False lim=[]
PC-C5 clean all reviewed: parse=complete synth=complete/approved gate=passed/approved/manual=False lim=[]
```

---

## V1-C6 — Egress (#315)

| Id | Lane | Egress observado | Proteção |
|---|---|---|---|
| CM-C6-01 | L-CHUNK (produção, consumer CT104) | payload inteiro: bloco `git diff` cru por arquivo (`payload_cost_model.py:170-196` → `chunk_payload_builder.py:169-178`) + paths relativos | regex secrets + redaction de path absoluto/home |
| CM-C6-02 | L-LEGACY (`/agent review llm`, `/agent ask`) | título/corpo do PR, paths crus, patch completo para PR pequena (≤3 arquivos, ≤30 000 chars), ≤2 000 chars de source head para certos arquivos frontend (`github_agent_review.py:1415-1589`) | regex inline (`:43-54,438-450`); não usa `app/agent_review/redaction.py` |
| CM-C6-03 | ambas | `output_safe_for_llm=True` constante (`cli.py:109`), checado como se fosse computado (`aiops-review-build-payloads.py:362`) | nenhuma |

`OBSERVED_CODE`. Implicação para a disposição: um revisor de código precisa transmitir o código revisado; uma garantia estrutural "nenhum source cru" é incompatível com a função da L-CHUNK. As opções de #315 permanecem A/B/C; B (aceitar risco) e C (desabilitar lane = redução funcional) são reservadas a decisão humana por #221.

---

## V1-C7 — Publication fidelity

Sem countermodel ativo no engine. Evidência `HISTORICAL` (AgentEscala #803). Limitação: o digest é produzido e verificado sobre o mesmo artefato CT104 — prova não-alteração entre producer e publisher, não atestação independente do resultado semântico.
