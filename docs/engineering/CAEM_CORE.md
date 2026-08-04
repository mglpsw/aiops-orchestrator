<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
body_provenance: historical_caem_2_1_projection
not_a_CAEM_3_0_F0_generated_view: true
The CAEM 3.0 F0 interface manifest produces only: contract-registry.json,
3 schemas, a Python catalog, and generated/CAEM_3_0_F0_INTERFACE.md — never
this file. This document's BODY is a preserved historical CAEM 2.1
operational overlay, NOT regenerated from or by CAEM 3.0 F0.
Norma pertence a mglpsw/caem, nunca a este repositório. authority_effect=none.
Single source of CAEM identity: config/caem/caem-3.0-f0.pin.json
CAEM interface: 3.0.0 F0, maturity=development_freeze, published=false
Interface manifest digest: sha256:6e2fdff772a16466b8af1934d03b4e29ec03aeae053ccb2bfa0b705f50ab48e5
Verify: ./.venv/bin/python scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check
CAEM 2.1.0 material (previously vendored here) is quarantined, read-only,
authority_effect=none: see .caem/quarantine/caem-2.1/.
-->

# CAEM CORE — historical AIOps-local operational overlay

# Contrato operacional para agentes de engenharia

## Princípio central

O agente recebe objetivo, escopo positivo, fronteira negativa, estado verificável, gates proporcionais, grants e encerramento objetivo.

## Antes de agir

- leia instruções e overlay;
- identifique issue/PR/objetivo;
- revalide repository/branch/base/HEAD/worktree;
- consulte forge/runtime vivos quando material;
- diferencie observado, implementado, especificado, planejado e inferido;
- derive superfícies, risco, gates e stop conditions;
- confirme grants para ações protegidas.

## Durante

- uma PR, um objetivo;
- menor slice funcional;
- sem alteração oportunista;
- writers e contratos canônicos;
- preview/dry-run write-zero;
- deny-by-default;
- LLM advisory e fail-closed;
- evidência por identity/environment/gate;
- falhas classificadas antes de reexecução.

## Estados

Não use “concluído” isoladamente. Informe implementação, validação, change request, slice, release, deployment e observação.

## Encerramento

Registre mudanças, não mudanças, base/HEAD/tested/deployed identities, gates, limitações, worktree, estado da PR, grants consumidos e próxima ação mínima. Não continue automaticamente para fase que exija nova autoridade.


## Matrizes de referência

# Precedência normativa e factual

## Normativa — o que é permitido

| Ordem | Fonte | Pode fazer |
|---:|---|---|
| 1 | lei, regulação e política institucional | impor limites não delegáveis |
| 2 | CAEM core | estabelecer guardrails universais |
| 3 | overlay do projeto | restringir e especializar para o domínio |
| 4 | task contract | delimitar a rodada atual |
| 5 | authority grant | habilitar ação protegida específica |
| 6 | instrução ad hoc | orientar dentro dos limites anteriores |

Camada inferior não amplia o que a superior proíbe.

## Factual — o que existe agora

| Ordem | Fonte | Uso |
|---:|---|---|
| 1 | runtime observado | saúde, revision implantada, configuração efetiva |
| 2 | forge/repositório vivos | branch, PR, checks, reviews, threads, HEAD |
| 3 | artefato/attestation | digest, build, migration head, proveniência |
| 4 | código/contratos/testes | comportamento e invariantes verificáveis |
| 5 | documentação atual | intenção e operação documentada |
| 6 | checkpoint datado | contexto histórico recente |
| 7 | memória de conversa | pista útil |
| 8 | inferência | hipótese explicitamente marcada |

## Conflito típico

`task contract` autoriza merge, mas o HEAD vivo mudou: a norma continua válida em abstrato, porém a condição factual bloqueia a ação. É necessário novo teste/review/grant ou atualização explícita.


# Eixos de execução e presets

## Eixos canônicos

| Eixo | Valores |
|---|---|
| `operation` | `analyze`, `plan`, `document`, `implement`, `validate`, `review`, `merge`, `deploy`, `release`, `operate`, `migrate`, `incident_response` |
| `work_unit` | `none`, `documentation`, `issue`, `pull_request`, `slice`, `release`, `environment`, `incident` |
| `validation_scope` | `none`, `documentary`, `focused`, `affected`, `integrated`, `release` |
| `environment_role` | `documentation`, `local`, `ci`, `ephemeral`, `canonical_dev`, `external_sandbox`, `production`, `local_or_ci`, `local_ci_and_ephemeral` |
| `risk` | `low`, `medium`, `high`, `critical` |
| `lifecycle_phase` | `discovery`, `planning`, `implementation`, `validation`, `review`, `merge`, `promotion`, `operation`, `retrospective` |

## Presets

| Preset | Operation | Work unit | Validation | Environment | Risk | Phase |
|---|---|---|---|---|---|---|
| `docs` | `document` | `documentation` | `documentary` | `documentation` | `low` | `implementation` |
| `pr-fast` | `implement` | `pull_request` | `focused` | `local_or_ci` | `low` | `implementation` |
| `pr-full` | `implement` | `pull_request` | `affected` | `local_ci_and_ephemeral` | `high` | `implementation` |
| `dev-iterate` | `deploy` | `environment` | `affected` | `canonical_dev` | `medium` | `validation` |
| `slice-close` | `validate` | `slice` | `integrated` | `canonical_dev` | `high` | `validation` |
| `release` | `release` | `release` | `release` | `production` | `critical` | `promotion` |

`ct104-iterate` é alias depreciado de `dev-iterate`; use o binding do profile para descobrir qual ambiente físico cumpre `canonical_dev`.

O preset é um default. Superfícies e risco podem exigir gates adicionais.


# Matriz de autoridade

## Efeito padrão

Todas as ações protegidas são negadas sem grant compatível.

| Ação | Target mínimo | Constraint típica | Evidência necessária |
|---|---|---|---|
| commit | repository/branch | escopo/path | diff e worktree |
| push | branch | expected HEAD | remote updated |
| abrir PR | repository/base | branch e issue | URL/ID e HEAD |
| marcar Ready | PR | HEAD + gates | checks/evidence completos |
| resolver thread | thread/PR | finding tratado | resposta + commit |
| merge | PR | método + protected HEAD | checks/reviews/threads vivos |
| fechar issue | issue | condição de aceite | links de entrega |
| deploy DEV | environment | revision | health/readiness/smoke |
| deploy PROD | environment | artifact digest/version | Go/No-Go + rollback |
| migration | database/environment | revision/range | backup + DB gate |
| ação destrutiva DB | database | comando exato | dupla confirmação + restore testado |
| provider externo | provider/environment | sandbox/real + payload class | idempotência + auditoria |
| proxy/DNS | resource | diff exato | dry-run + conflict check |
| próxima fase | phase | dependências | novo contrato |

## Invariantes

- `transferable` é sempre falso;
- grant para um alvo não vale para outro;
- grant para DEV não vale para PROD;
- grant para merge não vale para release/deploy;
- mudança do `subject_sha` invalida grant protegido por SHA;
- grant expirado, revogado ou consumido não pode ser reutilizado;
- conectividade, credencial e acesso técnico não são grants.


# Classificação de falhas

| Classe | Definição | Exemplo | Tratamento |
|---|---|---|---|
| product | comportamento existente incorreto | endpoint viola contrato | corrigir ou bloquear |
| regression | mudança atual introduziu falha | teste novo quebra no HEAD | corrigir antes de Ready |
| preexisting | baseline já falhava | teste falha na base igual | documentar, separar follow-up; bloquear se afeta aceite |
| environment | runner/ferramenta/serviço ausente | `/usr/bin/python3` sem pytest | corrigir ambiente, não produto |
| gate_unavailable | prova necessária não pode ser executada | browser/DSN ausente | marcar pendente; não inferir sucesso |
| contract | schema/versão/identity inconsistente | payload fora do schema | fail-closed |
| authority | ação sem grant ou fora do target | tentativa de deploy PROD | parar |
| security | risco de acesso/segredo/exposição | token em log | conter, sanitizar, escalar |
| data_integrity | risco de perda/corrupção | migration destrutiva | bloquear e preparar reversão |
| unknown | causa ainda não isolada | flaky não reproduzido | investigar; não reclassificar como verde |

## Regra operacional

Classifique antes de repetir. Reexecução sem hipótese diagnóstica aumenta custo e pode mascarar o problema.
