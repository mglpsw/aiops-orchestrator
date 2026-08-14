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
- HEAD observado: `7a6c6595b32373e2c297fd90dd7726974d69fa24`
  (squash merge de PR #230 — fecha C1-C4 e achados de review pós-merge na
  superfície de identidade do target-pack).
- Última release publicada: `v0.22.0` → `2ce1f45768b8779cb48ef8a302d4ed796349f0e5`,
  final, imutável, publicada em 2026-08-12.
- `master` está **8 commits à frente** da `v0.22.0`. Esse delta é **unreleased**.
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

Seis dos oito commits tocam exclusivamente superfície v2 sem tocar nenhum
arquivo v1 nem CAEM/shared (`8654272`, `7500966`, `5a1677f`, `79c7b2f`,
`7284528`, `7a6c659`); `8b20ae3` toca v2 **e** um módulo CAEM/shared
compartilhado (`app/common/strict_json.py`, `app/caem_consumer/f0.py`) — os
testes de equivalência visam preservar comportamento, mas isso permanece uma
superfície não-v2 tocada, não apenas v2; `7a6c659` toca exclusivamente
`target_pack_*_v2.py` e seus testes/schemas (v2); `da5a03b` (o fix v1) não
toca nenhum arquivo v2.

## Estado de referência

- **Roadmap canônico:** issue [`#46`](https://github.com/mglpsw/aiops-orchestrator/issues/46).
  Não existe segundo roadmap normativo no repositório; documentos de roadmap no tree
  são snapshots históricos.
- **CAEM:** single source of identity é `config/caem/caem-3.0-f0.pin.json` (CAEM 3.0 F0,
  `development_freeze`, `published=false`); material 2.1.0 anteriormente vendorizado em
  `.caem/` está em quarantine, `authority_effect=none` (`.caem/quarantine/caem-2.1/`);
  a norma pertence a `mglpsw/caem`.
- **AgentReview v1:** released, em maintenance/freeze. Baseline publicada é `v0.22.0`.
  O fix crítico `#225`/PR #227 está em `master` mas **ainda não está em nenhuma release
  publicada**. Release nova, repin e canário permanecem pendentes, cada um sob grant próprio.
- **AgentReview v2:** successor em desenvolvimento ativo. Não é GA, não é default,
  não é required check em nenhum target.
- **Target pack (`#203`):** primeira slice mergeada (`init`, `doctor`) mais o binding de
  operation plans (PR #228). `init` hoje faz seed apenas de profile/integration
  metadata — não instala o engine; `install-workflows` (deferido) é onde a
  instalação do engine propriamente aconteceria. PR #230 fechou C1-C4 e achados
  subsequentes de Codex shadow review na superfície de identidade (schema,
  `--target-repo` obrigatório em `doctor`, reconciliação do set target-owned,
  containment de symlink/path) — dívida pós-merge, não nova capability.
  `validate`/`conformance`/`install-workflows`/`upgrade`/`rollback` permanecem
  deferidos.
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

- implementação v1: `released_and_frozen`; fix `#225` `merged_unreleased`;
- release v1 pós-`#225`: `not_created`;
- repin/canário `#774`: `not_started`;
- `#221` (freeze/GA v1): `open`, bloqueada por release + repin + canário;
- implementação v2/target-pack: `in_progress`;
- `#199`/`#200`/`#201`/`#202`/`#203`/`#204`/`#205`: `open`;
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

Concluir a reconciliação documental (PR #229), depois decidir a versão da
próxima release v1 sob grant próprio. Merge, tag, release, repin, canário,
deploy e fechamento de `#221`/`#217`/`#199` permanecem retidos, cada um
exigindo grant nominal.
