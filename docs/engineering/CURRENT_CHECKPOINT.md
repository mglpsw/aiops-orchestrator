# CURRENT CHECKPOINT — AIOps/AgentReview

**Status:** `CANONICAL | CURRENT`
**Corte temporal:** 2026-08-14 (America/Sao_Paulo)
**Classe:** estado observado; revalidar HEAD, runs e issues antes de qualquer ação.

Este documento é importado pelo `CLAUDE.md` do repositório, portanto entra no
contexto de toda sessão de agente. Ele descreve o estado observado no corte, não
concede autoridade e não substitui consulta viva ao forge.

## Identidade viva observada

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- HEAD observado: `6d613cf7398a89d659694e150b9b5483483ed997`
  (squash merge de PR #237 — deriva a autoridade de ambiguidade YAML do
  TargetProfile do parser, encerrando PR-A de `#203-S2`; supersede PR #236
  e substitui PR #235 como registro forense). `#234` (refresh de checkpoint
  pós-H1) mergeou entre `abe034a` e este HEAD; o delta abaixo permanece
  ancorado em `abe034a` e não foi reexpandido por `#238`.
- Última release publicada: `v0.22.0` → `2ce1f45768b8779cb48ef8a302d4ed796349f0e5`,
  final, imutável, publicada em 2026-08-12.
- `master` está **11 commits à frente** da `v0.22.0`. Esse delta é **unreleased**.
- AgentReview é subsistema deste repositório, não repositório separado.

### Delta unreleased (`v0.22.0..master`)

| Commit | Escopo | Issue/PR |
|---|---|---|
| `8654272` | v2 — adversarial hardening de trusted checks | `#201-B3` / PR #218 |
| `8b20ae3` | v2 — authoritative CI provenance bridge; **também** extrai primitives de JSON estrito/digest de `app/caem_consumer/f0.py` para `app/common/strict_json.py` (superfície CAEM/shared, não só v2) | `#201-C0` / PR #219 |
| `7500966` | v2 — required-check readiness wiring | `#201-C` / PR #220 |
| `5a1677f` | v2 — canonicalize verified check order | PR #224 |
| `79c7b2f` | v2 — `agentreview-v2-target-pack`, `init`/`doctor` | `#203` / PR #223 |
| `7284528` | v2 — bind target pack init plans | PR #228 |
| `da5a03b` | **v1** — chunk planning por custo real de hunk, fail closed | `#225` / PR #227 |
| `7a6c659` | v2 — target-pack: fecha C1-C4 (schema, identidade `--target-repo`, reconciliação de set target-owned, symlink/path containment) + achados do Codex shadow review | `#205`/H1-A / PR #230 |
| `fdf89f7` | **docs** — reconciliação de verdade documental do repositório (estado canônico, navegação, separação v1/v2); diff exclusivamente `.md` | `#229` |
| `ffa3040` | **v1** — H1-B: fecha C5 (canonical path identity nos joins de contexto), C6 (projeção do brief minimum-budget), C7 (dedupe parity, P3), C8 (bloco binário não é hunk textual) e C10 (fallback não declara cobertura completa) | `#205`/H1-B / PR #231 |
| `abe034a` | v2 — H1-C: fecha C9 (fronteira runtime de `authority`; annotation Pydantic é inerte em parâmetro de função comum) | `#205`/H1-C / PR #233 |

Classificação de superfície dos 11 commits: **sete** tocam exclusivamente
superfície v2 (`8654272`, `7500966`, `5a1677f`, `79c7b2f`, `7284528`,
`7a6c659`, `abe034a`); `8b20ae3` toca v2 **e** um módulo CAEM/shared
compartilhado (`app/common/strict_json.py`, `app/caem_consumer/f0.py`) — os
testes de equivalência visam preservar comportamento, mas isso permanece uma
superfície não-v2 tocada, não apenas v2; **dois** tocam exclusivamente
superfície v1 sem tocar nenhum arquivo v2 (`da5a03b`, `ffa3040` — este último
também atualiza `docs/AGENT_REVIEW_ENGINE.md` para acompanhar a mudança de
comportamento de C10); `fdf89f7` é `.md`-only.

### Dívida pós-merge do checkpoint `#205` (C1-C10)

Todos os dez achados da auditoria pós-merge estão fechados e verificados:

| Achado | Superfície | Fechado por | Disposição |
|---|---|---|---|
| C1-C4 | v2 target-pack (identidade/schema) | H1-A / PR #230 | `fixed_and_verified` |
| C5, C6, C7, C8, C10 | v1 planner/projeção | H1-B / PR #231 | `fixed_and_verified` |
| C9 | v2 trusted-check authority boundary | H1-C / PR #233 | `fixed_and_verified` |

**O `POST_MERGE_REVIEW_DEBT_GATE` da `#205` NÃO está satisfeito**, porque uma
dívida P2 nova permanece viva:

```yaml
issue_232:
  severity: P2
  state: OPEN
  lane: AgentReview_v1
  intentionally_deferred: true
  blocks_203_implementation: false
  blocks_204_implementation: false
  requires_disposition_before_205_RC: true
```

`#232` foi levantada durante o review adversarial da H1-B (arquivo sem hunk
textual e fora de `must_review_files` ainda conta como coberto). A correção
tentada mostrou blast radius além do escopo H1-B e foi revertida
deliberadamente; ver a própria `#232` para o caminho proposto.

## Estado de referência

- **Roadmap canônico:** issue [`#46`](https://github.com/mglpsw/aiops-orchestrator/issues/46).
  Não existe segundo roadmap normativo no repositório; documentos de roadmap no tree
  são snapshots históricos.
- **CAEM:** single source of identity é `config/caem/caem-3.0-f0.pin.json` (CAEM 3.0 F0,
  `development_freeze`, `published=false`); material 2.1.0 anteriormente vendorizado em
  `.caem/` está em quarantine, `authority_effect=none` (`.caem/quarantine/caem-2.1/`);
  a norma pertence a `mglpsw/caem`.
- **AgentReview v1:** released, em maintenance/freeze. Baseline publicada é `v0.22.0`.
  Os fixes `#225`/PR #227 e H1-B/PR #231 estão em `master` mas **ainda não estão em
  nenhuma release publicada**. Release nova, repin e canário permanecem pendentes,
  cada um sob grant próprio. Dívida conhecida remanescente: `#232` (P2, deferida).
- **AgentReview v2:** successor em desenvolvimento ativo. Não é GA, não é default,
  não é required check em nenhum target.

#### Issue aberta ≠ pré-requisito técnico não satisfeito

`#200`, `#201` e `#202` permanecem **formalmente `OPEN`**, mas o motivo é
adoção pelo target, não lacuna de engine. Cada uma tem uma reconciliação
publicada na própria issue classificando seus critérios contra o código vivo:

```yaml
CORE_PREREQUISITES_FOR_203:
  issue_200: SATISFIED    # #200_CORE_SYNTHETIC: COMPLETE
  issue_201: SATISFIED    # #201_CORE: COMPLETE
  issue_202: SATISFIED    # #202_CORE: COMPLETE

formal_closure_pending:
  issue_200: canário semântico real do AgentEscala (AgentEscala #759)
  issue_201: adoção real pelo target (AgentEscala #750)
  issue_202: repin/migração dos consumidores para o codec upstream (AgentEscala #752)
```

Nenhuma dessas três bloqueia a implementação de `#203`. As checkboxes dessas
issues **não** são tratadas como máquina de estado do produto — a
classificação por critério nos comentários de reconciliação é a fonte de
verdade.

- **Target pack (`#203`):** primeira slice mergeada (`init`, `doctor`) mais o binding de
  operation plans (PR #228) e o hardening de identidade H1-A (PR #230). `init` hoje faz
  seed apenas de profile/integration metadata — não instala o engine;
  `install-workflows` (não implementado) é onde a instalação do engine propriamente
  aconteceria. O `max_supported_rollout_mode` desta versão do pack é `off`:
  `shadow_minimal`/`shadow_full` são opções de interface recusadas antes de preview ou
  apply. `validate`/`conformance`/`install-workflows`/`upgrade`/`rollback` permanecem
  **não implementados**. `#203` é a próxima fronteira de implementação ativa.
- **ProjectOps v1:** trilha separada de inteligência de CI, advisory e fail-safe; não
  revalidada neste corte.
- análise por LLM é advisory; `review-quality-gate.json` / `ReviewReadinessV2` e a CI
  determinística permanecem autoridades;
- CT104 executa AgentReview offline/toolrepo; CT102 executa runtime AIOps, nunca
  AgentReview runner;
- Router é transporte de inferência por contrato, sem autoridade sobre host ou verdict.

## Consumo pelo target repo (observado)

`mglpsw/AgentEscala` mantém dois pins independentes:

```text
v1  2ce1f45768b8779cb48ef8a302d4ed796349f0e5   (v0.22.0)  — lane operacional
v2  273864eaa01dfb708a5a26d3756e16c6cd918a9f   (v0.21.0)  — shadow
```

Repin é ação protegida e não foi executado neste corte.

## Estado vetorial

- implementação v1: `released_and_frozen`; fixes `#225` e H1-B `merged_unreleased`;
- release v1 pós-`#225`/H1-B: `not_created`;
- repin/canário `#774`: `not_started`;
- `#221` (freeze/GA v1): `open`, bloqueada por release + repin + canário;
- dívida pós-merge `#205` C1-C10: `fixed_and_verified` (H1-A + H1-B + H1-C);
- `POST_MERGE_REVIEW_DEBT_GATE` da `#205`: **não satisfeito** — `#232` P2 viva;
- `#232`: `open`, deferida deliberadamente, não bloqueia `#203`/`#204`;
- implementação v2/target-pack: `in_progress`; core `#200`/`#201`/`#202`
  `CORE_COMPLETE` e suficiente para `#203`;
- `#199`/`#200`/`#201`/`#202`/`#203`/`#204`/`#205`: `open` — `#200`/`#201`/`#202`
  por adoção de target, `#203` por implementação genuinamente incompleta,
  `#204`/`#205` não iniciadas;
- `#217` (classe residual de proveniência): `open`;
- `#213`, `#222`: `open`;
- release v2: `not_created`;
- deployment AgentReview: offline/advisory no CT104;
- última implantação registrada do runtime AIOps no CT102: `0.20.0`
  (`app/__init__.py`) — o que a árvore fonte deste checkout declara ter sido
  validado no último deploy, não uma observação viva do CT102; independente da
  tag do toolrepo — releases de toolrepo não implantam runtime;
- validação: `partial`;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

`#238` (institucionalização das lições de PR-A — ADR, postmortem, preflight,
corpus executável) está **em qualificação** como Draft PR sobre este
`master`. PR-B (`target validate`) permanece `BLOCKED_BY_238`: não é a
próxima ação autorizada enquanto `#238` não atingir sua disposição final.

Depois de `#238`, retomar a `#203` — completar o target pack instalável.
Os pré-requisitos de core (`#200`/`#201`/`#202`) estão satisfeitos e a dívida
C1-C10 está fechada; `#232` permanece aberta mas não bloqueia `#203`.

Merge, tag, release, repin, canário, deploy, adoção de target e fechamento de
`#221`/`#217`/`#199` permanecem retidos, cada um exigindo grant nominal. A
disposição de `#232` é exigida antes do release candidate da `#205`.
