# CURRENT CHECKPOINT — AIOps/AgentReview

**Corte temporal:** 2026-08-07 (America/Sao_Paulo)
**Classe:** estado observado/arquitetural; revalidar runs e issues antes de ação.

## Identidade viva observada

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- HEAD observado: `f409fd15e25cd7fcbed31fa8107cec419fd3827e` (squash merge de #211, ready-for-review → merged mesmo turno; base pré-merge `bc52aaddb9eedeae232e3abf07bb9efd7b3490e8`, mesmo HEAD de #201-B1/#210)
- AgentReview é subsistema deste repositório, não repositório separado.
- PRs abertas no corte: `#212` (`#201-B2`, executor isolado, rascunho, aguardando grant nominal de merge — ver abaixo).

## Estado de referência

- CAEM: single source of identity é `config/caem/caem-3.0-f0.pin.json` (CAEM 3.0 F0, `development_freeze`, `published=false`); material 2.1.0 anteriormente vendorizado em `.caem/` está em quarantine, `authority_effect=none` (`.caem/quarantine/caem-2.1/`); norma pertence a `mglpsw/caem`;
- AgentReview v1/release `v0.20.0` permanece baseline operacional, pinada por SHA completo nos target repos;
- AgentReview v2 está especificado/em consolidação por contracts binding, lossless multi-chunk, target profiles, dual-target conformance e Codex shadow;
- distribution epic `#199`, issue `#200` (core content/transport slices A/B/C): `core_synthetic_complete`, hardened por dois replan gates adversariais independentes pós-merge (9 achados confirmados, zero falso-positivo) via PR #211 (squash `f409fd1`) — ver `docs/checkpoints/AGENT_REVIEW_V2_200_ADVERSARIAL_AUDIT_FOLLOWUP.md`; issue `#200` continua OPEN, `partially_completed` pendente do canário do AgentEscala (`#763-A`), não fechada por este hardening;
- `#201` (trusted-check simulation): slice A (contratos/promotion authority) merged via PR #209 (`f001335`); slice B1 (simulador offline) merged via PR #210 (`bc52aad`); slice B2 (executor isolado real -- namespaces/rlimits/privilege-drop portáveis, comprovado neste sandbox de dev, NÃO no CT104) implementado como PR #212 (rascunho, branch `feat/201-b2-isolated-executor`), hardened por uma revisão independente pós-CI-verde (4 achados reais fechados nesta mesma slice: binding real do inventory a `authority_suite_digest`, remoção da heurística de OOM por sinal, hang de `communicate()`, regra explícita contra `authority=TRUSTED` em PR adversarial real até `#201-B3`) — ver `docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md`; PR #212 aguardando grant nominal de merge; `#201-B3` (hardening adversarial contra forja de exit code) e `#201-C` (wiring em `ReviewReadinessV2`) são as próximas ações após o merge de B2;
- ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe;
- análise por LLM é advisory; `review-quality-gate.json` e CI determinística permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca AgentReview runner; CT104 offline neste corte — critérios de `#201-B2` que exijam isolamento real do CT104 devem ser marcados `blocked_external: ct104_unavailable`, não falsificados via CT102;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Estado vetorial

- implementação baseline v0.20.0: `completed`;
- implementação v2/ProjectOps: `in_progress`;
- `#200` core (A/B/C): `core_synthetic_complete` + hardened (adversarial audit follow-up merged); `partially_completed` no nível da issue (canário AgentEscala pendente);
- `#201-A`/`#201-B1`: `merged`; `#201-B2`: `implemented, hardened (revisão independente pós-CI-verde), PR #212 aberta em rascunho, merge pendente de grant`;
- validação: `partial`;
- change request: `not_open` no corte;
- slice: `in_progress`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

Revalidar e mergear a PR #212 (`#201-B2`, grant nominal pendente); em seguida `#201-B3` (hardening adversarial contra forja de exit code por `conftest.py`/hook — precondição real para `authority=TRUSTED` em PR não confiável) antes de `#201-C` (wiring do resultado real do executor em `review_readiness_emission_v2`/`readiness_decision_v2`, que não deve tratar `authority=TRUSTED` como seguro contra PRs adversariais reais antes de `#201-B3`). CT104 permanece offline neste corte — a verificação do `harness_digest` contra uma imagem real pinada, e a garantia de isolamento sob o host de produção real, permanecem `blocked_external: ct104_unavailable` até CT104 estar acessível; revalidar quando disponível, não assumir sucesso. Revalidar issues `#80`, `#83–#89` e ProjectOps `#91–#95` antes de qualquer ação sobre essa trilha separada (não bloqueia `#201`).
