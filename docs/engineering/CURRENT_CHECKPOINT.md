# CURRENT CHECKPOINT — AIOps/AgentReview

**Corte temporal:** 2026-08-12 (America/Sao_Paulo), reconciliado após implementação de `#201-C`
(não mergeada neste corte — ver adendo abaixo).
**Classe:** estado observado/arquitetural; revalidar runs e issues antes de ação.

## Identidade viva observada

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- HEAD observado: `8b20ae37cb28c52c49f545d15d006de2f6086388` (squash merge de #219, `#201-C0`;
  base pré-merge `865427224b3d10a9ce4858183e5848351672935e`, squash merge de #218, `#201-B3`).
  `master` avançou `8654272 → 8b20ae3` neste corte, verificado ao vivo (parent exato, tree
  idêntico ao HEAD testado `00fb8b3e`).
- AgentReview é subsistema deste repositório, não repositório separado.
- PRs abertas no corte: PR #220 (`feat/201-c-required-check-readiness-wiring` → `master`,
  **draft**, `#201-C`). `code_head_reviewed_through_round_8`:
  `2d39d0686371414038be8dd61fbce25521a7bb5f` — ambos os checks (`Validate repository`,
  `AgentReview release gates`) verdes nesse SHA. Commits documentais posteriores avançam o
  HEAD sem alterar código; o HEAD vivo da PR é autoridade e deve ser lido do forge, nunca
  deste documento. Review adversarial em andamento (8 rodadas, 0 consecutivas limpas) — ver
  adendo.

## Estado de referência

- CAEM: single source of identity é `config/caem/caem-3.0-f0.pin.json` (CAEM 3.0 F0,
  `development_freeze`, `published=false`); material 2.1.0 anteriormente vendorizado em `.caem/`
  está em quarantine, `authority_effect=none` (`.caem/quarantine/caem-2.1/`); norma pertence a
  `mglpsw/caem`;
- AgentReview v1/release `v0.20.0` permanece baseline operacional, pinada por SHA completo nos
  target repos;
- distribution epic `#199`, issue `#200` (core content/transport slices A/B/C):
  `core_synthetic_complete`, hardened — issue `#200` continua OPEN, `partially_completed` pendente
  do canário do AgentEscala (`#763-A`), não revalidado nesta sessão;
- `#201` (trusted-check simulation): slices A/B1/B2/B3/C0 **mergeadas** (`f001335`, `bc52aad`,
  `57e0b05`, `8654272`, `8b20ae3`); `#201-B3` operacionalmente `BLOCKED_BY_CT104` (isolamento real
  sob host persistente não comprovado fora do sandbox); `#201-C0` fechou o bypass de proveniência
  exercido pela `#201` (`#217` residual permanece OPEN para a classe completa); `#201-C`
  (wiring em `ReviewReadinessV2`) **implementada nesta sessão**, não mergeada — ver adendo;
  issue `#201` continua OPEN;
- ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe, não
  revalidada nesta sessão;
- análise por LLM é advisory; `review-quality-gate.json`/`ReviewReadinessV2` e CI determinística
  permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca AgentReview
  runner; CT104 permanece offline neste corte;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Estado vetorial

- implementação baseline v0.20.0: `completed`;
- implementação v2/ProjectOps: `in_progress`;
- `#200` core (A/B/C): `core_synthetic_complete` + hardened; `partially_completed` no nível da
  issue (não revalidado nesta sessão);
- `#201-A`/`#201-B1`/`#201-B2`/`#201-B3`/`#201-C0`: `merged`; `#201-B3` operational closure
  `BLOCKED_BY_CT104`; `#217` exercised path `closed_by_pr_219`, residual class `open`;
- `#201-C`: `implemented_awaiting_merge_review` (PR #220 aberta, draft;
  `code_head_reviewed_through_round_8` = `2d39d06`, CI real verde nesse SHA; review adversarial
  em andamento, 8 rodadas, 0 consecutivas limpas, aguardando duas consecutivas limpas no mesmo
  HEAD) — ver adendo;
- `#203`: `not_started`;
- validação: `partial`;
- change request: `not_open` no corte;
- slice: `in_progress`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- observation: métricas/false-positive loop a ampliar, não revalidado nesta sessão.

## Adendo — `#201-C` implementada nesta sessão, não mergeada

Grant nominal do dono do repositório, escopado à implementação de `#201-C` conforme a Execution-
Ready Engineering Specification rev.2.1 (auditada e aprovada em duas rodadas prévias — ver `#201`).
Implementação feita em worktree novo (`/opt/agent-tools/ar-201-c-readiness-wiring`), a partir de
`origin/master` @ `8b20ae37`, branch `feat/201-c-required-check-readiness-wiring`, seis commits de
implementação + um de checkpoint/receipt + oito commits de correção do review adversarial (15 no
total até este corte, sem contar commits puramente documentais; a contagem cresce a cada rodada
não-limpa). PR #220 aberta como **draft**, vinculada a `#201`;
`code_head_reviewed_through_round_8` = `2d39d0686371414038be8dd61fbce25521a7bb5f`.

Entrega: novo módulo `required_check_readiness_v2.py` como choke point entre `#201-C0` e a
readiness — deriva `required_check_names` exclusivamente de um `TargetProfileV2` confiável ligado
a `identity.profile_hash`, nunca do caller; `readiness_decision_v2._apply_required_check_
assessment_v2` com a precedência ratificada (`STALE` soberano, `CONFIRMED` finding preserva
`BLOCKED_CODE`, required check não satisfeito força `MANUAL_REQUIRED`+`POLICY_FAILURE`, inclusive
downgrade deliberado de `BLOCKED_PIPELINE`); `review_readiness_emission_v2.produce_review_
readiness_v2` como único caminho público de construção de `ReviewReadinessV2` (o antigo
`emit_review_readiness_v2` virou `_assemble_review_readiness_v2`, interno); `run_synthetic_
review_v2` perdeu o parâmetro `checks` ungated (a última porta residual que `#217` nomeava);
`scripts/aiops-review-quality-gate-v2.py` reescrito para emitir artifact `manual_required` real
(exit 0) em vez de falhar quando um required check não tem submissão legítima
(`CLI_EXIT_SUCCESS != READINESS_READY`, documentado).

Prova mecânica: `test_required_check_readiness_arch_v2.py`, 8 testes AST/call-graph provando sítio
único de construção, caller único do assembler, travessia obrigatória de C0, nenhum entry point
público aceitando assessment ou nomes caller-supplied, nenhum `except` ao redor da recusa de C0, e
nenhuma fixture criando rota positiva alcançável em produção (`temporary_until_203`). Dois asserts
verificados por mutação durante a implementação (reintrodução de `except`/parâmetro
caller-supplied — ambos pegos com a mensagem certa).

Nenhum contrato congelado alterado; export de schemas byte-idêntico; pin CAEM F0 verde.
`tests/agent_review/ tests/evals/` combinado: 1749 passed, 16 skipped, 2 failed (classe
`environment` — `sudo` ausente no sandbox, `test_isolated_executor_v2.py`, fora do escopo desta
slice, mesma classe de falha que a baseline pré-edição). Evidência medida em
`code_head_reviewed_through_round_8` = `2d39d06`, e reconfirmada idêntica no commit documental
seguinte; corrente na correção da rodada 8 de review adversarial; será revalidada a cada rodada
subsequente. CI real do GitHub (`Validate repository`, `AgentReview release gates`) verde nesse
mesmo SHA.

Detalhes completos: `docs/checkpoints/AGENT_REVIEW_V2_201C_READINESS_WIRING.md` e
`reports/agent-review-v2-201c-readiness-wiring-receipt.json`.

**Correção (reconciliação de evidência):** push, abertura de PR draft, CI real do GitHub e o loop
de review adversarial **são** cobertos por este grant — já realizados (ver acima). O que
permanece **fora** do escopo: merge, tag, release, deploy, repin, ativação de capacidade CT104,
início de `#203`, fechamento de `#201`/`#217`/`#199`.

## Próxima ação mínima

Continuar o loop de review adversarial (rodada 9 em diante) até duas rodadas consecutivas limpas
no mesmo HEAD — condição de parada do grant, ainda não atingida (8 rodadas completas, 0
consecutivas limpas). Merge, tag, release, deploy, repin, ativação de capacidade e fechamento de
`#201`/`#217`/`#199` permanecem retidos, cada um exigindo grant nominal próprio. `#203` não deve
começar antes do fechamento operacional de `#201-C` (código + CI real + review adversarial
estável).
