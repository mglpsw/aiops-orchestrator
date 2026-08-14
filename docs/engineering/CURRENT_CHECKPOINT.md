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
- HEAD observado: `da5a03b4b444f00319e553b613cafcb44a98b132`
  (squash merge de PR #227, `#225` — chunk planning por custo real de hunk).
- Última release publicada: `v0.22.0` → `2ce1f45768b8779cb48ef8a302d4ed796349f0e5`,
  final, imutável, publicada em 2026-08-12.
- `master` está **7 commits à frente** da `v0.22.0`. Esse delta é **unreleased**.
- AgentReview é subsistema deste repositório, não repositório separado.

### Delta unreleased (`v0.22.0..master`)

| Commit | Escopo | Issue/PR |
|---|---|---|
| `8654272` | v2 — adversarial hardening de trusted checks | `#201-B3` / PR #218 |
| `8b20ae3` | v2 — authoritative CI provenance bridge | `#201-C0` / PR #219 |
| `7500966` | v2 — required-check readiness wiring | `#201-C` / PR #220 |
| `5a1677f` | v2 — canonicalize verified check order | PR #224 |
| `79c7b2f` | v2 — `agentreview-v2-target-pack`, `init`/`doctor` | `#203` / PR #223 |
| `7284528` | v2 — bind target pack init plans | PR #228 |
| `da5a03b` | **v1** — chunk planning por custo real de hunk, fail closed | `#225` / PR #227 |

Seis dos sete commits tocam exclusivamente superfície v2; `da5a03b` (o fix v1) não
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
  operation plans (PR #228). `validate`/`conformance`/`install-workflows`/`upgrade`/
  `rollback` permanecem deferidos.
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
- runtime AIOps implantado no CT102: `0.20.0` (`app/__init__.py`), independente da
  tag do toolrepo — releases de toolrepo não implantam runtime;
- validação: `partial`;
- observation: métricas/false-positive loop a ampliar.

## Próxima ação mínima

Concluir a reconciliação documental, depois decidir a versão da próxima release v1
sob grant próprio. Merge, tag, release, repin, canário, deploy e fechamento de
`#221`/`#217`/`#199` permanecem retidos, cada um exigindo grant nominal.
