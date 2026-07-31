# CURRENT CHECKPOINT — AIOps/AgentReview

**Corte temporal:** 2026-07-30 12:32:30 (America/Sao_Paulo)  
**Classe:** estado observado/arquitetural; revalidar runs e issues antes de ação.

## Identidade viva observada

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- HEAD observado: `620242eec8c665b0c990cdf3c2f32881b034be5e`
- AgentReview é subsistema deste repositório, não repositório separado.
- PRs abertas no corte: nenhuma retornada pela consulta agregada.

## Estado de referência

- AgentReview v1/release `v0.20.0` permanece baseline operacional, pinada por SHA completo nos target repos;
- AgentReview v2 está especificado/em consolidação por contracts binding, lossless multi-chunk, target profiles, dual-target conformance e Codex shadow;
- ProjectOps v1 permanece trilha separada de inteligência de CI, advisory e fail-safe;
- análise por LLM é advisory; `review-quality-gate.json` e CI determinística permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca AgentReview runner;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Estado vetorial

- implementação baseline v0.20.0: `completed`;
- implementação v2/ProjectOps: `in_progress`;
- validação: `partial`;
- change request: `not_open` no corte;
- slice: `in_progress`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

Revalidar issues `#80`, `#83–#89` e ProjectOps `#91–#95`, SHAs pinados, coverage e último quality gate antes de implementar ou publicar.
