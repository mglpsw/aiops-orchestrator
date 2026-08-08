# CURRENT CHECKPOINT — AIOps/AgentReview

**Corte temporal:** 2026-08-08 (America/Sao_Paulo)
**Classe:** estado observado/arquitetural; revalidar runs e issues antes de ação.

## Identidade viva observada

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- HEAD observado: `57e0b056c2c2a4bf3ddfab12a84f9eb46278c42d` (squash merge de #212, `#201-B2`; base pré-merge `5037a49916decb30ccbce4330c696686d61e56db`, que é por sua vez o squash merge de #214, engine v1, sobre `f409fd15e25cd7fcbed31fa8107cec419fd3827e` = HEAD pós-#211). `master` avançou `f409fd1 → 5037a49 → 57e0b05`, cada avanço um squash exato, verificado via `git merge-base --is-ancestor`.
- AgentReview é subsistema deste repositório, não repositório separado.
- PRs abertas no corte: `#215` (esta própria PR de reconciliação documental — desaparece desta lista após seu próprio merge) e `#216` (`docs(release): prepare v0.22.0`, trilha docs/release separada, criada por outra sessão; não altera nem bloqueia a sequência `#201-B3` → `#201-C`). `#214` e `#212` mergeadas neste corte.

## Estado de referência

- CAEM: single source of identity é `config/caem/caem-3.0-f0.pin.json` (CAEM 3.0 F0, `development_freeze`, `published=false`); material 2.1.0 anteriormente vendorizado em `.caem/` está em quarantine, `authority_effect=none` (`.caem/quarantine/caem-2.1/`); norma pertence a `mglpsw/caem`;
- AgentReview v1/release `v0.20.0` permanece baseline operacional, pinada por SHA completo nos target repos;
- AgentReview v2 está especificado/em consolidação por contracts binding, lossless multi-chunk, target profiles, dual-target conformance e Codex shadow;
- distribution epic `#199`, issue `#200` (core content/transport slices A/B/C): `core_synthetic_complete`, hardened por dois replan gates adversariais independentes pós-merge (9 achados confirmados, zero falso-positivo) via PR #211 (squash `f409fd1`) — ver `docs/checkpoints/AGENT_REVIEW_V2_200_ADVERSARIAL_AUDIT_FOLLOWUP.md`; issue `#200` continua OPEN, `partially_completed` pendente do canário do AgentEscala (`#763-A`), não fechada por este hardening;
- AgentReview v1 (AgentEscala#675): três defeitos de proveniência/evidência corrigidos (prosa do modelo entrando no namespace determinístico, `required` vs `optional` colapsados, `_plan_status` testando `limitations` por truthiness) via PR #214 (squash `5037a49`), v2 intocado, merge/tag/release não autorizados nessa rodada — repin de target segue trabalho separado, bloqueado por release não publicada;
- `#201` (trusted-check simulation): slice A (contratos/promotion authority) merged via PR #209 (`f001335`); slice B1 (simulador offline) merged via PR #210 (`bc52aad`); slice B2 (executor isolado real -- namespaces/rlimits/privilege-drop portáveis, comprovado neste sandbox de dev, NÃO no CT104) **merged via PR #212 (squash `57e0b05`)**, hardened por três rodadas de revisão independente pós-CI-verde, todas contra HEAD real, todas com CI real verde no HEAD final (binding do inventory a `authority_suite_digest` + identidade de chave, remoção da heurística de OOM por sinal, PGID-after-reap, hang de `communicate()`/descendente órfão, arquivo de pgid-report writable pelo próprio código sob teste, lockdown desse arquivo fail-open, `TRUSTED` sobre isolamento fraco sem separação real de uid) — ver `docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md` e comentário de reconciliação em `#201`; `#201-B2` = `foundation_complete`; `#201-B3` tem dois critérios de aceite obrigatórios (forja de exit code + containment de lifetime de processo via PID namespace, este último achado real confirmado na revisão da B2 e deliberadamente não implementado nela) antes de `#201-C` (wiring em `ReviewReadinessV2`) poder começar; issue `#201` continua OPEN;
- ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe;
- análise por LLM é advisory; `review-quality-gate.json` e CI determinística permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca AgentReview runner; CT104 offline neste corte — critérios de `#201-B2` que exijam isolamento real do CT104 devem ser marcados `blocked_external: ct104_unavailable`, não falsificados via CT102;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Estado vetorial

- implementação baseline v0.20.0: `completed`;
- implementação v2/ProjectOps: `in_progress`;
- `#200` core (A/B/C): `core_synthetic_complete` + hardened (adversarial audit follow-up merged); `partially_completed` no nível da issue (canário AgentEscala pendente);
- AgentReview v1 (AgentEscala#675): `merged` (PR #214, squash `5037a49`), `release_ready` (release/tag/repin não executados);
- `#201-A`/`#201-B1`/`#201-B2`: `merged`; `#201-B2` = `foundation_complete` (PR #212, squash `57e0b05`); `#201-B3`: `planned, not_started` — dois critérios obrigatórios (ver abaixo);
- validação: `partial`;
- change request: `not_open` no corte;
- slice: `in_progress`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

Planejar e implementar `#201-B3`, com **dois critérios de aceite obrigatórios, nenhum opcional**: (1) hardening adversarial contra forja de exit code por `conftest.py`/hook — precondição real para `authority=TRUSTED` em PR não confiável; (2) containment de lifetime de processo — teardown por PID namespace (ou equivalente) provando zero descendentes sobreviventes, incluindo um que escape via `setsid()`/double-fork próprio, achado real confirmado por revisão da #201-B2 (PR #212, já mergeada) e deliberadamente não implementado nessa slice (subsistema de rastreamento de pgid já produziu cinco bugs reais; CT104 offline impede validação do teardown real antes do merge) — reclassificado de "follow-up recomendado" para critério obrigatório de fechamento da `#201-B3`. `#201-C` (wiring do resultado real do executor em `review_readiness_emission_v2`/`readiness_decision_v2`) não deve começar, e ativação de capacidade no CT104 não deve prosseguir, antes dos dois critérios acima fechados; `#201-C` também não deve invocar o executor contra conteúdo adversarial real de PR sob o fallback fraco (userns não-privilegiado) mesmo com `authority=UNTRUSTED_ADVISORY` — essa autoridade só impede promoção da evidência, não protege o host contra o processo adversarial em si. CT104 permanece offline neste corte — a verificação do `harness_digest` contra uma imagem real pinada, e a garantia de isolamento sob o host de produção real, permanecem `blocked_external: ct104_unavailable` até CT104 estar acessível; revalidar quando disponível, não assumir sucesso. Revalidar issues `#80`, `#83–#89` e ProjectOps `#91–#95` antes de qualquer ação sobre essa trilha separada (não bloqueia `#201`).
