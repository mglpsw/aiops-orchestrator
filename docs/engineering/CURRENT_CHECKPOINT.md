# CURRENT CHECKPOINT — AIOps/AgentReview

**Status:** `CANONICAL | CURRENT`
**Corte temporal:** 2026-08-16 (America/Sao_Paulo)
**Classe:** estado observado; revalidar HEAD, runs e issues antes de qualquer ação.

Este documento é importado pelo `CLAUDE.md` do repositório, portanto entra no
contexto de toda sessão de agente. Ele descreve o estado observado no corte, não
concede autoridade e não substitui consulta viva ao forge.

## Identidade — âncora vs. HEAD vivo

Este documento não pode conter de forma estável o próprio SHA resultante do
commit que o contém: publicar essa afirmação dentro do commit que a produz a
torna obsoleta no instante do merge — exatamente o defeito que `70ba4e3`
(`#234`) cometeu ao ancorar o checkpoint na sua própria base e deixá-lo
desatualizado. Três identidades são mantidas deliberadamente separadas:

```yaml
state_anchor:
  implementation_anchor:
    sha: d454e8f2d272b9edb011513b4a8f5d4e89ece4c2
    source: "PR #244 squash merge"
    meaning: >-
      estado congelado do repositório contendo a implementação já descrita
      abaixo (validate canônico em master, #203-C2) — não uma alegação de
      HEAD vivo atual. O commit de reconciliação de documentação que
      introduz esta revisão (PR-D0) é doc-only e deliberadamente não entra
      nesta contagem.

  release_baseline:
    version: v0.22.0
    sha: 2ce1f45768b8779cb48ef8a302d4ed796349f0e5
    published: 2026-08-12
    immutable: true

  checkpoint_document:
    identity: containing_commit
    role: documentation_only
    excluded_from_implementation_anchor: true
    note: >-
      o commit que introduz esta revisão do documento é doc-only e
      deliberadamente não é adicionado à tabela de delta como se fosse parte
      da implementação que a tabela descreve.

  live_master:
    source_of_truth: GitHub
    hardcoded_sha: false
    instruction: >-
      revalidar via forge antes de qualquer ação; nunca assumir este
      documento como HEAD atual.
```

- Repositório canônico: [`mglpsw/aiops-orchestrator`](https://github.com/mglpsw/aiops-orchestrator)
- Branch padrão: `master`
- AgentReview é subsistema deste repositório, não repositório separado.

### Delta unreleased observado até o `implementation_anchor` (`v0.22.0@2ce1f457..d454e8f2`)

No estado-âncora acima, o intervalo `v0.22.0..<implementation_anchor>` contém
**18 commits** (`git rev-list --count v0.22.0..d454e8f2d272b9edb011513b4a8f5d4e89ece4c2`).
Esse delta é **unreleased**.

| Commit | Escopo | Issue/PR |
|---|---|---|
| `d454e8f2` | v2 — `agent-review target validate`: plano de observação tipado, orçamento agregado, inventário de 17 checks (11 avaliáveis, 6 permanentemente `unavailable`); três rounds de review adversarial fechados (`STOP_REDESIGN: false` em todos); também atualiza `docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md` | `#203-C2` / PR #244 |
| `2f512d2` | v2 — target-pack: canonicaliza `Repository`/`RelativePath` como invariantes de construção em `TargetInstallReceiptV2`, disjunção de ledgers de ownership; toca dois schemas (`agent-review.target-install-receipt.v2`, `agent-review.target-pack-operation-plan.v2`) | `#203-C1` / PR #243 |
| `f07ff03` | **docs** — corrige atribuição de transição de issue no checkpoint; diff exclusivamente `.md` | `#238` / PR #241 |
| `078f078` | **docs** — reconcilia checkpoint após `#239`; diff exclusivamente `.md` | `#238` / PR #240 |
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
| `70ba4e3` | **docs** — refresh do checkpoint após fechamento H1 (`README.md`, `docs/PROJECT_STATUS.md`, `docs/engineering/CURRENT_CHECKPOINT.md`); diff exclusivamente `.md`; âncora deixada em `abe034a`, tornando-se stale — o defeito que este próprio documento agora corrige | `#234` |
| `6d613cf` | v2 — deriva ambiguidade YAML de `TargetProfile` do próprio parser stock, substituindo a autoridade anterior (`#235`→`#236` superseded); introduz `_CollisionRefusingSafeLoaderV2` | `#203-S2` PR-A / PR #237 |
| `0cdb961` | **v2, superfície de teste/docs apenas** — institucionaliza as lições de `#237` em ADR normativo, postmortem, Structural Change Preflight reutilizável e corpus de regressão executável (49 casos, 8 rounds de review adversarial exact-HEAD, 25 achados P2 fechados); zero mudança em `app/**` ou `schemas/**`, verificado por identidade de byte | `#238` / PR #239 |

Classificação de superfície dos 18 commits: **nove** tocam superfície de
código de produção v2 exclusivamente (`8654272`, `7500966`, `5a1677f`,
`79c7b2f`, `7284528`, `7a6c659`, `abe034a`, `6d613cf`, `d454e8f2` — este
último também toca um único arquivo `.md`, o spec operativo do `#203`,
como parte da própria qualificação do comando que introduz); `8b20ae3`
toca v2 **e** um módulo CAEM/shared compartilhado
(`app/common/strict_json.py`, `app/caem_consumer/f0.py`) — os testes de
equivalência visam preservar comportamento, mas isso permanece uma
superfície não-v2 tocada, não apenas v2; **um** (`2f512d2`) toca v2
**e** dois schemas (`agent-review.target-install-receipt.v2`,
`agent-review.target-pack-operation-plan.v2`) — mudança aditiva, nenhum
schema existente antes de `#203` foi tocado; **dois** tocam exclusivamente
superfície v1 sem tocar nenhum arquivo v2 (`da5a03b`, `ffa3040` — este
último também atualiza `docs/AGENT_REVIEW_ENGINE.md` para acompanhar a
mudança de comportamento de C10); **quatro** são `.md`-only (`fdf89f7`,
`70ba4e3`, `f07ff03`, `078f078`); **um** (`0cdb961`) é v2 de
teste/documentação apenas — fixtures (`CORPUS.json`, `.yamlcase`),
módulos pytest sob `tests/agent_review/`, ADR/postmortem/preflight e
`CHANGELOG.md` — sem tocar `app/**` ou `schemas/**`; não é agrupado com os
`.md`-only porque inclui fixtures e código de teste, não apenas prosa.

Soma: 9 + 1 + 1 + 2 + 4 + 1 = 18.

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
deliberadamente; ver a própria `#232` para o caminho proposto. A própria
`ffa3040` (H1-B) toca essa superfície — é onde a tentativa revertida vive,
conforme sua mensagem de squash. Não revalidada além do estado `OPEN` neste
corte; verificado que **nenhum commit após `ffa3040`** no delta acima toca
`chunk_payload_builder.py`, `semantic_chunker.py`, `chunk_result_parser.py`
ou `payload_cost_model.py` (`git log ffa3040..0cdb961 -- <esses arquivos>`
vazio) — a ausência de revalidação é sobre o período pós-H1-B, não sobre o
delta inteiro.

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
- **AgentReview v2 — processo institucionalizado (`#238`, PR #239):** a sequência
  PR-A de autoridade YAML de `TargetProfile` (`#235`→`#236` superseded→`#237`
  shipped) está documentada em
  [`docs/adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md`](../adr/ADR_AGENT_REVIEW_V2_TARGET_PROFILE_YAML_AUTHORITY.md)
  e
  [`docs/engineering/AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md`](AGENT_REVIEW_V2_YAML_AUTHORITY_POSTMORTEM.md),
  com corpus executável de regressão em
  `tests/agent_review/fixtures/target_profile_yaml/`. O Structural Change
  Preflight reutilizável que essa sequência motivou está em
  [`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`](STRUCTURAL_CHANGE_PREFLIGHT.md)
  e é a autoridade **exclusiva** de critérios/limiares STOP/REDESIGN —
  `PROJECT_OVERLAY.md` aponta para ele sem duplicar.

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
  operation plans (PR #228), o hardening de identidade H1-A (PR #230), os contratos de
  identidade `#203-C1` (`Repository`/`RelativePath` como invariantes de construção,
  PR #243) e `agent-review target validate` `#203-C2` (PR #244) — leitura-apenas,
  target-only, coerência local, não é prova independente de proveniência upstream;
  inventário de 17 checks, 11 avaliáveis localmente, 6 permanentemente `unavailable`.
  `init` hoje faz seed apenas de profile/integration metadata — não instala o engine;
  `install-workflows` (não implementado) é onde a instalação do engine propriamente
  aconteceria. O `max_supported_rollout_mode` desta versão do pack é `off`:
  `shadow_minimal`/`shadow_full` são opções de interface recusadas antes de preview ou
  apply. `conformance`/`install-workflows`/`upgrade`/`rollback` permanecem
  **não implementados**. `#203` é a próxima fronteira de implementação ativa; o
  trabalho de `#237`/`#239` fechou a autoridade de leitura YAML que `#203-S2` PR-A
  precisava; PR-B (`validate`) foi entregue como `#203-C1`+`#203-C2`; `conformance`
  (PR-C) ainda não avançou.
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

Repin é ação protegida e não foi executado neste corte; pins não
revalidados diretamente no repositório alvo neste corte, herdados do
checkpoint anterior.

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
- `#203-S2` PR-A (autoridade YAML de `TargetProfile`): `merged_unreleased`
  (`6d613cf`, PR #237); PR-B (`validate`) foi entregue como `#203-C1`
  (contratos de identidade, `2f512d2`, PR #243) seguido de `#203-C2`
  (`agent-review target validate`, `d454e8f2`, PR #244) — **mergeada e
  canônica em `master`**; PR-C (`conformance`) **não iniciada**;
- `#238` (institucionalização das lições de PR-A): estado é **forge-derived**,
  não fixado neste documento — ver bloco `issue_238` abaixo. Implementação
  entregue via PR #239 (squash `0cdb961`) e reconciliação de checkpoint via
  PR #240 (squash `078f078`); escopo e histórico de review na tabela de
  delta acima e nas próprias PRs, não repetidos aqui. Transições
  automáticas de estado da issue foram observadas após o merge de #239 e,
  de novo, após o merge de #240 — em ambos os casos, `closingIssuesReferences`
  da PR estava vazio no momento do merge. A causa verificada é uma
  referência de palavra-chave de fechamento de issue apontando para `#238`,
  embutida na mensagem de squash gerada (que concatena o corpo de todos os
  commits constituintes neste repositório), não um link de desenvolvimento
  de sidebar — hipótese anterior deste documento, agora **retratada** por
  falta de evidência de suporte. Cada transição automática foi detectada e
  revertida manualmente.

```yaml
issue_238:
  source_of_truth: GitHub
  instruction: revalidar estado vivo antes de qualquer ação
  durable_fact: >-
    a entrega consiste em PR #239 mais a reconciliação de checkpoint PR
    #240; a disposição final da issue é uma ação de forge separada e
    controlada, não uma consequência automática de squash.
```

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

## Próxima ação mínima (histórico — escopado à âncora `0cdb961`/`#238`)

**Nota de reconciliação:** esta seção descreve o estado de planejamento
observado na âncora anterior deste documento (`0cdb961`, checkpoint de
`#238`), antes de `implementation_anchor` avançar para `d454e8f2`. A
condicional sobre "PR-B/`target validate`" abaixo já foi resolvida por
entrega real — ver `#203-C1`/PR #243 e `#203-C2`/PR #244 na tabela de
delta acima — independentemente de qual disposição `#238` tinha no
momento em que esta seção foi escrita. Preservada como histórico, não
como estado atual de PR-B.

A entrega técnica de `#238` está completa (PR #239, PR #240 e esta própria
reconciliação, todas já mergeadas ou em qualificação). O estado de `#238` é
consultado no forge e **não é fixado neste documento** — nem como "ainda
pendente", nem como "já concluído", já que qualquer uma das duas afirmações
envelhece no exato momento em que a outra passa a valer:

- se a consulta viva indicar que `#238` **ainda não** está com disposição
  final registrada, essa disposição permanece uma ação de forge separada,
  deliberada, sujeita a grant nominal — as transições automáticas de
  estado eventualmente observadas (ver `issue_238` acima) não contam como
  essa ação controlada;
- se a consulta viva indicar `#238` com disposição final registrada, o
  blocker processual para `PR-B` está removido, e a próxima implementação
  (`#203` — completar o target pack instalável, começando por PR-B/`target
  validate`) só pode começar sob **novo grant**, ramificando da `master`
  viva revalidada no momento em que começar.

Em ambos os casos, o `implementation_anchor` vigente **no momento em que
este texto foi escrito** (`0cdb961`) permanecia apenas a identidade
**histórica** daquela institucionalização — nunca uma base operacional
para PR-B, pelo mesmo motivo pelo qual este documento evita se
autorreferenciar. `implementation_anchor` já avançou desde então para
`d454e8f2` (ver bloco `state_anchor` no topo deste documento), justamente
porque PR-B foi entregue. Os pré-requisitos de core (`#200`/`#201`/`#202`)
estavam satisfeitos, a dívida C1-C10 estava fechada, e a autoridade de
leitura YAML que PR-B consumiu (`#237`) estava institucionalizada via
`#238`; `#232` permanece aberta mas não bloqueia `#203` — fatos duráveis,
independentes do estado de `#238`.

Merges futuros de PRs que referenciam `#238` devem sanitizar título e
corpo de squash contra referências de palavra-chave de fechamento de
issue antes de mergear — não confiar apenas no corpo da PR, e não deixar
o GitHub sintetizar a mensagem de squash a partir dos commits
constituintes sem revisão prévia; esse foi exatamente o vetor das duas
transições automáticas registradas acima.

Enquanto o forge não reportar `#238` com disposição final registrada, essa
disposição exige grant nominal próprio, distinto do grant de merge desta
ou de qualquer PR anterior. Tag, release, repin, canário, deploy e adoção
de target permanecem retidos incondicionalmente, cada um exigindo grant
nominal; o mesmo vale para a disposição de `#221`/`#217`/`#199`. A
disposição de `#232` é exigida antes do release candidate da `#205`.
