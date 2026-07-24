# AgentReview v2 — roadmap de conclusão, Codex e adoção multi-target

**Estado em 2026-07-23 (America/Sao_Paulo)**  
**Roadmap issue:** [#46](https://github.com/mglpsw/aiops-orchestrator/issues/46)  
**Epic de execução:** [#80](https://github.com/mglpsw/aiops-orchestrator/issues/80)

## 1. Decisão executiva

O `aiops-orchestrator` mantém duas superfícies separadas:

```text
CT102/runtime produtivo
  diagnose -> plan -> dry-run -> approval -> read-only run -> audit

CT104/AgentReview toolrepo
  artifacts -> redaction -> evidence -> chunks -> parsing -> synthesis -> gate
```

O AgentReview v1 de `v0.20.0` permanece operacional durante uma janela explícita de compatibilidade. O track v2 deve completar binding, cobertura lossless, profiles reutilizáveis, instalação mínima e conformance em dois target repos antes de qualquer migração regular.

Regra de autoridade:

```text
Modelos propõem observações.
Contratos e binding determinam validade.
Lifecycle registra decisões no HEAD avaliado.
Readiness/quality gate decide.
O humano continua responsável pelo merge e pelas exceções.
```

## 2. Estado comprovado

### 2.1. V1 concluído

`v0.20.0` entrega:

- intake e redaction offline;
- semantic chunk plan;
- bounded chunk payloads;
- structured parser;
- síntese final;
- `review-quality-gate.json` como autoridade pós-síntese;
- telemetry e false-positive signatures;
- sugestões de contrato sempre `manual_only` e não aplicadas automaticamente.

### 2.2. Fundação v2 incorporada

| Entrega | Estado | Referência |
|---|---|---|
| Contratos run/payload/response/profile/readiness v2 | concluída | PR #81, squash `6bb140e625452fc32f2ed39e220dc60639dc2f79` |
| Coverage binding sem promoção e required checks não vazios | concluída | PR #82, squash `4797fc8ba3eda6dbc2d7be599a981f7c1c5a9efc` |
| Consumers/parser v2 | pendente | #83 |
| Manifest/fragments e multi-chunk lossless | pendente | #84 |
| Profile loader/migrator e lock mínimo | pendente | #85 |
| Conformance AgentEscala/InterLeitos | pendente | #86 |
| Codex guidance/subagents | pendente, paralela | #87 |
| Benchmark/calibração | pendente | #88 |
| Release/adoption | pendente | #89 |

O v2 ainda não está ativo nos workflows de target repo. V1 e v2 não podem ser misturados implicitamente.

## 3. Arquitetura alvo

```text
Target repository
  -> profile, policy, review packs e contracts da base confiável
  -> evidence workflow sem secrets
  -> artifact allowlisted e ligado ao HEAD

AIOps v2 no CT104
  -> strict target-profile loader
  -> deterministic run identity
  -> typed fragment manifest
  -> lossless multi-chunk planner
  -> bounded payloads v2
  -> verified response binding before findings
  -> parser, synthesis e finding lifecycle
  -> ReviewReadinessV2 / quality gate
  -> sanitized publication bundle

Optional reviewers
  -> Codex local/GitHub ou Agent Router review-only
  -> external observations ligadas ao HEAD/fragment
  -> sem autoridade direta sobre readiness

Target publisher
  -> revalida PR aberta, merged state, HEAD e checks
  -> publica resultado advisory de forma idempotente
```

## 4. Caminho crítico

### Fase A — core v2 offline

#### #83 — binding em consumers/parser

Resultado:

- seleção explícita v1/v2;
- payload e response integralmente revalidados antes de qualquer finding;
- stale, cross-run, payload mismatch e mixed versions aceitam zero findings;
- reason codes e precedência estáveis;
- adapter v1 temporário sem fabricar prova v2.

#### #84 — manifest e chunking lossless

Resultado:

- fragments estáveis por path/range/diff hash;
- vários chunks por grupo e por arquivo;
- hunk/linha alterado de `must_review` nunca é shrinker;
- budget insuficiente replaneja ou bloqueia;
- união dos ranges cobertos equivale exatamente aos ranges esperados.

#### #85 — profile loader e ambiente mínimo

Resultado:

- profile/policy strict, deny-unknown e hash-verificados;
- target não consegue enfraquecer hard boundaries;
- migrador v1 -> v2 explícito e não destrutivo;
- `requirements-agent-review.lock` exato, hash-pinned e instalado com `--require-hashes`;
- engine sem `if repo == AgentEscala/InterLeitos`;
- consumo por SHA completa lowercase de 40 caracteres.

#83, #84 e #85 podem avançar em PRs separadas. Cada PR deve declarar dependências reais e preservar a suíte v1.

### Fase B — conformance dual-target

#### #86 — AgentEscala + InterLeitos

Mesmo engine e mesmas CLIs, variando apenas:

- target profile;
- policy;
- review packs;
- domain contracts;
- fixtures sintéticas.

Estados exercitados:

```text
ready
blocked_code
blocked_pipeline
manual_required
stale
```

O E2E é offline: sem provider, Router, Codex, GitHub write, CT102 ou dados clínicos reais.

### Fase C — Codex e calibração

#### #87 — instruções e subagentes

Criar:

```text
AGENTS.md
app/agent_review/AGENTS.md
app/agent_router/AGENTS.md
schemas/agent-review/AGENTS.md
.github/AGENTS.md
.codex/config.toml
.codex/agents/*.toml
```

Subagentes read-only:

- contract reviewer;
- trust-boundary reviewer;
- test reviewer;
- target-integration reviewer.

Codex começa como developer tool e shadow reviewer. Não é required check e não altera o gate.

#### #88 — benchmark AIOps × Codex × humano

Medir em corpus sintético:

- recall e precisão por severidade;
- false-positive rate;
- cobertura total e `must_review`;
- stale/cross-run rejection;
- aprovação falsa;
- duplicação entre chunks/reviewers;
- findings sem path/range/evidência;
- estabilidade determinística.

Concordância de modelos não confirma automaticamente um finding. A verdade do benchmark vem de expectations versionadas e evidência reproduzível.

### Fase D — release e adoção

#### #89 — release pinável

Sequência de rollout:

```text
offline conformance
-> shadow v1/v2 por target
-> advisory idempotente
-> default v2 somente por decisão explícita baseada em métricas
-> retirada v1 em release e issue futuras
```

A release deve fornecer:

- release candidate e release final;
- schemas/fixtures verificados;
- SHA canônica e rollback ref;
- matriz v1/v2;
- instalação pelo lock mínimo;
- shadow dry-run nos wrappers;
- nenhuma alteração do runtime CT102.

## 5. AgentEscala

Integrações locais:

- `mglpsw/AgentEscala#675` — evidência e cobertura determinísticas;
- `mglpsw/AgentEscala#678` — isolamento entre código não confiável e secrets.

Responsabilidades do target:

- manter regras 24H/12H/10–22H em contracts/review packs;
- produzir evidence bundle secretless;
- separar job não privilegiado, análise base-owned e publisher;
- carregar profile/policy da base confiável;
- pin do toolrepo por SHA completa;
- revalidar PR/HEAD antes de publicar;
- não reimplementar parser, synthesis, lifecycle ou gate.

A fixture da #86 deve cobrir regras críticas de escala e regressões sintéticas derivadas de reviews anteriores, sem mover lógica do produto para o AIOps.

## 6. InterLeitos

Epic consumidora: `mglpsw/interleitos#19`.

Trilha local:

| Issue | Resultado |
|---|---|
| InterLeitos #28 | `.aiops/**`, ADR, threat model, review packs, DLP e CODEOWNERS |
| InterLeitos #29 | workflow `pull_request` secretless e artifact allowlisted |
| InterLeitos #30 | análise base-owned, AIOps v2 e Router review-only, sem GitHub write |
| InterLeitos #31 | publisher GitHub-hosted, shadow, calibração e advisory |
| InterLeitos #32 | observabilidade operacional sanitizada e paralela |

Dependências AIOps:

```text
#83 + #84 + #85
-> #86 dual-target conformance
-> #88 calibration
-> #89 release/adoption
```

Invariantes específicas:

- produto/devex chama-se RepoReview/`developer_review`;
- AgentReview é componente upstream e não cria autoridade clínica/regulatória;
- somente dados inequivocamente sintéticos em fixtures, provider e calibração;
- DLP bloqueia CPF, CNS, identidade/contexto de paciente, contato, nascimento, narrativa clínica plausível, secrets, dumps e paths locais;
- suspeita inconclusiva de PHI bloqueia o envio inteiro;
- nenhum acoplamento a CELK/GERINT;
- observabilidade não inclui conteúdo clínico nem executa remediação.

## 7. Codex

### Uso imediato

- VS Code/CLI: `/review` orientado por `AGENTS.md`;
- subagentes read-only para investigação paralela;
- GitHub: `@codex review` em shadow mode;
- findings usados na #88 como lane separada.

### Uso posterior

A #63 poderá normalizar observações Codex, Router e humanas por um contrato provider-neutral. Para serem consideradas, observações precisam estar ligadas a:

- repo e PR;
- HEAD exato;
- run;
- path/range/fragment;
- diff hash;
- evidência sanitizada.

Mesmo depois da calibração:

- outro reviewer não corrige schema/binding/coverage/DLP falhos;
- consenso não substitui prova;
- discordância relevante leva a revisão humana;
- Codex não auto-aprova, remove blocker, faz merge ou deploy.

## 8. Pós-v2 não bloqueante

### #63 — independent confirmation

Contrato provider-neutral para observações Codex/Router/humanas, inicialmente em shadow mode.

### #64 — Validation Evidence

Pré-review semântico opcional com `authoritative=false`, sem promoção de coverage/lifecycle e com DLP anterior ao transporte.

Nenhuma das duas bloqueia a primeira release offline da #89.

## 9. Hard boundaries

```text
AgentReview somente no CT104/toolrepo.
CT102 nunca staging ou executor de PR.
Sem shell livre, SSH, docker exec ou comando produzido por modelo.
Sem deploy, remediação, auto-merge ou required check probabilístico.
Sem provider direto no AgentReview.
Sem /v1/chat/ingest.
Sem GitHub write pelo core AIOps.
Sem profile/policy privilegiados vindos da PR.
Sem raw diff/prompt/payload/response, secrets, paths locais ou PHI públicos.
Sem blocker sem evidência reproduzível e ligada ao HEAD.
Sem aplicação automática de suggested contract updates.
```

## 10. Gates de conclusão

- [ ] #83, #84 e #85 concluídas com suíte v1 preservada;
- [ ] #86 verde para os dois targets no mesmo engine;
- [ ] schemas e outputs canônicos byte-reproducíveis;
- [ ] instalação limpa `--require-hashes`;
- [ ] 100% dos fragments `must_review` cobertos ou bloqueados;
- [ ] zero finding aceito de outro run/HEAD/payload/target;
- [ ] #88 publica baseline e thresholds;
- [ ] #89 fornece SHA canônica e rollback;
- [ ] AgentEscala #678 resolve trust boundary antes de credenciais;
- [ ] InterLeitos #28/#29 precedem análise base-owned;
- [ ] shadow/advisory antes de qualquer default v2;
- [ ] CT102 e runtime produtivo permanecem inalterados.

## 11. Issues históricas encerradas

Após a reorganização:

- #42 foi fechada: telemetry/fp loop já entregue;
- #43 foi fechada: quality gate entregue por #59/#60; saldo em #63;
- #44 foi fechada como superseded por #64;
- #45 foi fechada: redaction/coverage v1 absorvida pelo baseline; saldo lossless em #84 e benchmark em #88.

Não reabrir fases v1 para implementar v2. Usar a epic #80 e suas child issues.