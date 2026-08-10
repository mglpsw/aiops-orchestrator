# CURRENT CHECKPOINT — AIOps/AgentReview

**Corte temporal:** 2026-08-08 (America/Sao_Paulo), reconciliado após implementação de `#201-B3`.
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
- `#201` (trusted-check simulation): slice A (contratos/promotion authority) merged via PR #209 (`f001335`); slice B1 (simulador offline) merged via PR #210 (`bc52aad`); slice B2 (executor isolado real) **merged via PR #212 (squash `57e0b05`)**, `foundation_complete` — ver `docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md`; slice B3 (adversarial hardening — fronteira de autoridade + PID-namespace containment, incluindo a Emenda A1 do broker privilegiado para a estratégia sudo-elevada) **implementada nesta sessão** (branch `claude/agentreview-v2-hardening-plan-74kva0`, ainda não em PR/merge neste corte) — ver `docs/checkpoints/AGENT_REVIEW_V2_201B3_ADVERSARIAL_HARDENING.md`; `#201-B3` fecha os dois critérios obrigatórios que `#201-B2` deixou abertos (forja de exit code, containment de lifetime de processo), comprovados sob três contas reais (root/direto, sudo-elevado via broker, userns fraco) — CT104 permanece `blocked_external: ct104_unavailable` para a prova específica do host persistente; `#201-C` (wiring em `ReviewReadinessV2`) e `#201-C0` (proveniência de CI autoritativa, `#217`) não iniciados; issue `#201` continua OPEN;
- ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe;
- análise por LLM é advisory; `review-quality-gate.json` e CI determinística permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca AgentReview runner; CT104 offline neste corte — critérios de `#201-B2` que exijam isolamento real do CT104 devem ser marcados `blocked_external: ct104_unavailable`, não falsificados via CT102;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Estado vetorial

- implementação baseline v0.20.0: `completed`;
- implementação v2/ProjectOps: `in_progress`;
- `#200` core (A/B/C): `core_synthetic_complete` + hardened (adversarial audit follow-up merged); `partially_completed` no nível da issue (canário AgentEscala pendente);
- AgentReview v1 (AgentEscala#675): `merged` (PR #214, squash `5037a49`), `release_ready` (release/tag/repin não executados);
- `#201-A`/`#201-B1`/`#201-B2`: `merged`; `#201-B2` = `foundation_complete` (PR #212, squash `57e0b05`); `#201-B3`: `implemented, adversarially_hardened` nesta sessão (não em PR/merge neste corte) — dois critérios obrigatórios fechados (ver `docs/checkpoints/AGENT_REVIEW_V2_201B3_ADVERSARIAL_HARDENING.md`); `#201-C0` (`#217`) e `#201-C`: `not_started`;
- validação: `partial`;
- change request: `not_open` no corte;
- slice: `in_progress`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

`#201-B3` está implementada nesta sessão (branch `claude/agentreview-v2-hardening-plan-74kva0`), com os dois critérios obrigatórios fechados e comprovados sob três contas reais (root/direto; sudo-elevado via `trusted_check_broker_v2` — Emenda A1; userns fraco/sem privilégio). Nenhum contrato congelado alterado; exportação de schemas v2 byte-idêntica; `ci_validate.sh` seções 7 e 8 verdes; pin CAEM F0 ok. Merge **não autorizado** nesta rodada — aguarda grant nominal específico, mesma disciplina de `#201-B2`.

Próxima ação mínima: push da branch, abertura de PR **rascunho** para `#201-B3`, confirmação de CI real do GitHub verde no HEAD final, comentário de reconciliação em `#201`. Após isso, com `#201-B3` operacionalmente fechada (código + CI real + conformidade CT104 quando disponível): (1) `#201-C0` (`#217` — proveniência autorizada para `RequiredCheckResultV2`, união tipada `TrustedHostPromotion ∪ AuthoritativeCIPromotion`, sidecar aditivo, sem tocar contrato congelado); depois (2) `#201-C` (wiring em `ReviewReadinessV2`). Nenhum dos dois deve começar antes do fechamento operacional de `#201-B3`, e ativação de capacidade no CT104 não deve prosseguir antes disso. CT104 permanece offline neste corte — a verificação do `harness_digest` contra uma imagem real pinada, a garantia de isolamento sob o host de produção real (incluindo especificamente o caminho sudo-elevado/broker da Emenda A1, não testado fora deste sandbox), e a permissão do perfil LXC do CT104 para namespaces PID/user aninhados permanecem `blocked_external: ct104_unavailable` até CT104 estar acessível; revalidar quando disponível, não assumir sucesso. Revalidar issues `#80`, `#83–#89` e ProjectOps `#91–#95` antes de qualquer ação sobre essa trilha separada (não bloqueia `#201`).
