# `#201-C` — Required-check readiness wiring

**Classe:** checkpoint de implementação. Review adversarial **em andamento**
— 5 rodadas executadas até agora, cada uma achando e corrigindo um problema
genuíno; aguardando duas rodadas consecutivas limpas no mesmo HEAD (condição
de parada do grant). Ver "Estado vetorial" e "Próxima ação mínima".

## Por que esta slice existe

`#201-C0` (PR #219, merged, `master` = `8b20ae37cb28c52c49f545d15d006de2f6086388`)
fechou a fronteira de proveniência entre uma *claim* de required check e um
`RequiredCheckResultV2` legitimado: nenhum check chega ao quality gate v2 sem
ser re-derivável de um snapshot adquirido, de uma política base-owned e da
identidade do run. O que `#201-C0` deliberadamente não fez foi conectar esse
conjunto legitimado à decisão de readiness:

- `compute_readiness_decision_v2` nunca leu `checks`;
- `ReviewReadinessV2.validate_state_invariants` já exigia, para `ready`,
  `checks` não-vazio e todo verde — então um check vermelho não produzia
  *estado*, produzia `pydantic.ValidationError`;
- `run_synthetic_review_v2` aceitava um parâmetro `checks` **sem nenhuma
  verificação** — a última porta ungated que a classe residual de `#217`
  nomeava.

`#201-C` fecha essas três lacunas, sob o plano rev.2.1 (auditado e aprovado
antes da implementação — ver `#201` para o histórico completo da auditoria e
das duas rodadas de emenda, rev.2 e rev.2.1).

## Fronteira inegociável, preservada

```text
#201-C MAY CONNECT AUTHORITY.
#201-C MUST NEVER CREATE AUTHORITY.
```

## O que foi implementado

### Novo módulo: `required_check_readiness_v2.py`

O choke point entre `#201-C0` e a readiness:

- `_assess_required_checks_v2` — pura, total, sobre checks já legitimados.
  Decide `SATISFIED`/`FAILED`/`AUTHORITY_NOT_ESTABLISHED` a partir de um
  conjunto de nomes obrigatórios.
- `_verify_and_assess_required_checks_v2` — o choke point real. Congela as
  submissões em tuplas antes de qualquer outra coisa; deriva
  `required_check_names` **exclusivamente** de um `TargetProfileV2` lido de
  um `target_profile_root` confiável e ligado a `identity.profile_hash`;
  chama `reassemble_and_verify_required_checks_v2` (`#201-C0`) sem nenhum
  `except` ao redor.

Nenhum parâmetro `required_check_names`/`policies`/`loaded_policy` existe em
nenhuma função pública do caminho de readiness — a completude nunca vem do
caller.

### `readiness_decision_v2.py` — precedência

`_apply_required_check_assessment_v2`, aditiva. `compute_readiness_decision_v2`
permanece **intocada** (preserva `evals/harness.py`/`aiops_projection.py`).
Precedência ratificada:

```text
1. STALE                          → soberano, nunca tocado
2. CONFIRMED code finding          → BLOCKED_CODE (coexiste com POLICY_FAILURE)
3. required check FAILED/NOT_ESTABLISHED → MANUAL_REQUIRED + POLICY_FAILURE
                                      (downgrade deliberado de BLOCKED_PIPELINE,
                                       registrado, não uma necessidade do contrato)
4-7. inalterado
```

### `review_readiness_emission_v2.py` — único caminho de construção

```text
produce_review_readiness_v2          <- único entry point público
  -> _verify_and_assess_required_checks_v2   (#201-C0)
  -> _apply_required_check_assessment_v2      (precedência)
  -> _assemble_review_readiness_v2            (antigo emit_review_readiness_v2,
                                                agora interno)
       -> ReviewReadinessV2(...)              <- único sítio de construção
```

### `review_transport_v2.py` e `scripts/aiops-review-quality-gate-v2.py`

`run_synthetic_review_v2` perdeu o parâmetro `checks` ungated; passa a
verificar a si mesma via `produce_review_readiness_v2`. O CLI perdeu
`_validate_required_check_provenance`/`_validate_required_checks_complete` —
ambas as responsabilidades migraram para `produce_review_readiness_v2`.

**Mudança de contrato de CLI, deliberada:** um required check sem submissão
legítima não falha mais o CLI — emite um artifact real `manual_required` +
`policy_failure` e sai 0. Um submission forjado continua sem artifact, saindo
1.

```text
CLI_EXIT_SUCCESS != READINESS_READY
```

Sem consumer produtivo do CLI v2 hoje (confirmado por busca global durante a
auditoria) — mudança segura de aplicar agora.

### Contratos

Zero mudança. `RequiredCheckResultV2`, `ReviewReadinessV2`, `RunIdentityV2`,
`RunOriginV2`, `TargetProfileV2`, `TargetPoliciesV2`, `TrustedCheckPlanV2`,
`TrustedCheckResultV2`, `ReadinessReasonV2`, `ReadinessStateV2`,
`RequiredCheckConclusionV2`, `PipelineDegradationCauseV2`, `ReadinessBlockerV2`
— todos intactos. Export de schemas byte-idêntico
(`export-agent-review-v2-schemas.py --check`). Pin CAEM F0 inalterado
(`verify-caem-f0-pin.py --check`).

`RequiredCheckReadinessAssessmentV2` é estado interno, não-wire: sem
`schema_id`, nunca exportado, nunca aceito por nenhuma função pública.

## Testes

- `test_required_check_readiness_v2.py` — 17 testes: composição pura (Class B)
  e o choke point real contra `#201-C0` sem patch (Class A), incluindo o
  ataque de nomes reduzidos (C-T23) provado com dois profiles reais
  distintos.
- `test_readiness_decision_v2.py` — +17 testes cruzando os 6 formatos de
  decisão de conteúdo × 3 status de assessment.
- `test_review_readiness_emission_v2.py` — testes de `_assemble_review_
  readiness_v2` preservados (renomeados); +2 testes de `produce_review_
  readiness_v2` via C0 real.
- `test_required_check_readiness_arch_v2.py` — **novo**, 8 testes AST/
  call-graph: sítio único de construção; caller único do assembler e do
  helper de assessment; travessia obrigatória de C0; nenhum entry point
  público aceita assessment; nenhum `except` ao redor do refuse da C0;
  completude nunca caller-supplied; nenhuma fixture cria rota positiva
  alcançável em produção (`temporary_until_203`). Dois dos sete asserts
  verificados por mutação (reintrodução de `except`/de parâmetro
  caller-supplied) durante a implementação — ambos pegaram a regressão
  exata, com a mensagem certa.
- `test_review_transport_v2.py` — 4 testes de `run_synthetic_review_v2`
  migrados para o choke point real; um deles reescrito de "emite READY com
  green check à mão" para "emite MANUAL_REQUIRED quando autoridade não foi
  estabelecida".
- `test_aiops_review_quality_gate_v2_cli.py` — 3 dos 32 testes exigiram
  mudança real (dois reason codes movidos; um redesenhado como prova
  positiva do novo artifact `manual_required`); os outros 29 permanecem
  válidos sem alteração de comportamento.
- `test_v2_dual_target_e2e.py` — seção Class B existente relabelada
  explicitamente; nova seção Class A com 2 testes (`agent_escala`,
  `interleitos`) provando o mesmo estado honesto através dos dois profiles
  reais shipped.

Contagens acima refletem o estado no fim da implementação (commit 9); as
rodadas de review adversarial abaixo adicionaram testes de regressão
próprios a `test_readiness_decision_v2.py` e `test_aiops_review_quality_gate_v2_cli.py`
— ver "Review adversarial" para a lista.

## Review adversarial

5 rodadas executadas até o momento deste checkpoint, cada uma achando pelo
menos um problema genuíno, reproduzido antes de corrigido, com um red test
próprio e verificação por mutação:

1. string de detalhe sem limite excedendo o bound `SafeText` do contrato
   (`PipelineDegradationCauseV2.detail`) com um conjunto grande de required
   checks — corrigido com truncamento determinístico.
2. downgrade `BLOCKED_PIPELINE→MANUAL_REQUIRED` sobre uma decisão contendo
   `SCHEMA_FAILURE`/`TRANSPORT_FAILURE` produzindo um `ReadinessDecisionV2`
   irrepresentável, só detectado várias chamadas depois como
   `pydantic.ValidationError` opaco — corrigido com recusa imediata e
   nomeada (`ReadinessDecisionError`).
3. duas variantes do sufixo `(+N more)` corrompendo-se a si mesmo em
   `_joined_with_budget_v2`; `ReadinessDecisionError` não capturado no CLI,
   vazando um traceback bruto — ambos corrigidos.
4. `produce_review_readiness_v2` chamando a fronteira `#201-C0` mesmo com
   `decision.state is STALE`, o que podia recusar por drift de profile em
   vez de emitir `STALE` limpo — corrigido movendo o short-circuit de STALE
   para antes da chamada à C0.
5. o guard de irrepresentabilidade do achado #2 cobria só o ramo
   `MANUAL_REQUIRED`; `BLOCKED_CODE` tem seu próprio conjunto de reason
   codes permitido (que não é superconjunto do de `MANUAL_REQUIRED` —
   ambos proíbem coisas diferentes), e uma combinação
   `state=BLOCKED_CODE, reason_codes=(CONFIRMED_CODE_FINDING,
   FINDING_CONFIRMATION_REQUIRED)` reproduzia o mesmo defeito de forma
   assimétrica — corrigido generalizando o guard para checar qualquer um
   dos dois ramos contra o conjunto seguro que de fato lhe corresponde.
6. o mesmo guard checava só membership de `reason_codes`, não unicidade de
   `(reason_code, component)` em `pipeline.causes` nem de `blocker_id` em
   `blockers` — uma decisão já carregando uma causa/blocker
   `policy_failure`/`required_checks`/`required-checks` colidia com a que
   esta função anexa, mesmo passando no guard de reason codes — corrigido
   checando as duas colisões antes de construir. Na mesma rodada: dois
   pontos cegos no próprio arquivo de teste de arquitetura --
   `_annotation_names` não reconhecia `X | None`/`Optional[X]`/forward-ref
   string (só `X`/`module.X`), e `_compares_state_to_ready_like` não
   reconhecia a forma de associação `resultado.state in {...}` (só
   `==`/`is`) -- ambos corrigidos com testes unitários diretos provando a
   lacuna antes da correção. Também corrigido: `FORBIDDEN_COMPLETENESS_
   PARAM_NAMES` faltava `"required_checks"`, apesar do docstring do
   próprio teste afirmar cobri-lo; e uma auto-contradição em
   `docs/AGENT_REVIEW_V2_REVIEW_READINESS_EMISSION.md` (texto histórico
   ainda descrevendo `emit_review_readiness_v2` como atual, enquanto a
   seção `#201-C` no fim do mesmo arquivo já dizia "não existe mais sob
   esse nome").
7. `_joined_with_budget_v2` perdia todo o conteúdo quando um único nome
   sozinho (mais o próprio sufixo) já excedia o `budget` — o fallback
   antigo descartava o nome inteiro, devolvendo um `"(+1 more)"` sem
   nenhuma informação sobre qual check estava em falta/falhando, mesmo
   havendo espaço de sobra no `budget` para mostrar um prefixo truncado
   útil. Como `SafeText` permite nomes de até 512 caracteres contra um
   `budget` de 200, um nome sozinho acima do limite é um cenário
   realisticamente alcançável, não só sintético — corrigido truncando o
   próprio nome em vez de descartá-lo.

Nenhuma rodada chegou ainda a "limpa" — a condição de parada do grant (duas
rodadas consecutivas limpas no mesmo HEAD) ainda não foi atingida.

## Gates executados

| Gate | Resultado |
|---|---|
| `tests/agent_review/ tests/evals/` combinado | 1745 passed, 16 skipped, 2 failed (classe `environment`, sudo ausente no sandbox — `test_isolated_executor_v2.py`, arquivo fora do escopo de `#201-C`) |
| `export-agent-review-v2-schemas.py --check` | OK |
| `verify-caem-f0-pin.py --check` | OK |
| `ruff` | não canônico neste repositório (ausente de `requirements-dev.txt`) — gate pulado, não fabricado |
| CI remota (`aiops-ci`) | verde na rodada 6 (`2258d41`); pendente revalidação no HEAD da rodada 7 |

Baseline (mesmo ambiente, HEAD `8b20ae3`, antes de qualquer edição): idêntica
classificação — 1681/16/2 em `tests/agent_review/ tests/evals/` juntos,
mesmas 2 falhas de ambiente. Nenhuma regressão introduzida. Figura de 1745
corrente na correção da rodada 7 de review adversarial (ganho de +64 desde o
baseline: testes novos + red tests de cada rodada de correção); será
superada pelo HEAD final quando as duas rodadas limpas consecutivas forem
alcançadas.

## O que `#201-C` não faz

- não cria autoridade — Path A continua sem caller produtivo; Path B continua
  recusado incondicionalmente por `verify_independent_semantic_judge_v2`;
- não prova `READY`/`BLOCKED_PIPELINE` alcançável via C0 real — deliberadamente
  diferido (`temporary_until_203`, ver assert 7 do arquivo de arquitetura);
- não autentica a origem de `ReadinessDecisionV2`/`findings` — trust
  assumption preexistente de C1/C2, documentada, não absorvida;
- não fecha `#217` — fecha o único caminho ungated residual que restava
  (`run_synthetic_review_v2`), mas `#217` exige prova de exaustividade da
  classe inteira, não ausência de caminho conhecido;
- não inicia `#203`, não ativa CT104, não toca contratos congelados.

## Estado vetorial

```text
#201_A=MERGED
#201_B1=MERGED
#201_B2=MERGED
#201_B3=MERGED (operational closure BLOCKED_BY_CT104)
#201_C0=MERGED
#201_C_IMPLEMENTATION=COMPLETE
#201_C_ADVERSARIAL_REVIEW=IN_PROGRESS (5 rodadas; aguardando duas consecutivas limpas no mesmo HEAD)
#201_C=IMPLEMENTED_AWAITING_MERGE_REVIEW
#217=OPEN_RESIDUAL_NOT_BLOCKING_C
#203=NOT_STARTED
CT104_CONFORMANCE=NOT_CLAIMED
AUTHORITATIVE_PYTEST_PROMOTION=UNAVAILABLE_BY_DESIGN
RELEASE=NOT_AUTHORIZED
```

## Próxima ação mínima

Abrir Draft PR vinculada a `#201`, aguardar CI real, solicitar review
adversarial. Merge, tag, release, deploy, repin, ativação CT104, início de
`#203` e fechamento de `#201`/`#217`/`#199` permanecem fora do grant desta
rodada.
