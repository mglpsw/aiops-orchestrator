# `#201-C0` — Authoritative CI provenance bridge

**Classe:** implementação observada nesta sessão. Revalidar runs/issues antes de qualquer ação.
**Base:** `master` @ `865427224b3d10a9ce4858183e5848351672935e` (squash de PR #218, `#201-B3`).
**Plano canônico:** [`#201` comment 5258778428](https://github.com/mglpsw/aiops-orchestrator/issues/201#issuecomment-5258778428).

## Por que esta slice existe

`#201-B3` ratificou uma fronteira de classe, não um detector:

```text
controls(subject, success_signal)  ⇒  not authoritative(success_signal)
```

pytest é `subject_code`, logo permanentemente `UNTRUSTED_ADVISORY` no executor isolado. Mas os
dois target profiles vivos declaram `required_checks: [pytest]`. Sem uma fonte autoritativa
independente, a readiness desses targets nunca seria conclusiva.

Em paralelo, `#217` registra um defeito **pré-existente** que a B3 expôs (não criou): nenhuma
camada validava a proveniência de um `RequiredCheckResultV2`. O gate comparava **apenas nomes**
(`scripts/aiops-review-quality-gate-v2.py:242-245`), e o contrato congelado
(`contracts_v2.py:1057-1062`) não carrega identidade de produtor.

```text
Containment  ≠  Evidence  ≠  Provenance  ≠  Authority
   #201-B3         #201-B2      #201-C0       #201-C
```

## O que foi implementado

| Commit | Entrega |
|---|---|
| C0-1 | `app/common/strict_json.py`; `f0.py` passa a aliasar, zero hash drift |
| C0-2 | `RequiredCheckProvenanceV2` + `compute_required_check_digest_v2` |
| C0-3 | `.aiops/authoritative-checks.v2.yaml` base-owned + fixtures dual-target |
| C0-4 | snapshot offline, seleção determinística de tentativa, mapping fechado |
| C0-5 | assembler host-owned + verificador + matriz adversarial |
| C0-6 | gate endurecido + acquirer host-owned |
| C0-7 | este checkpoint, docs e reconciliação |

### Decisões estruturais

- **União tipada de duas fontes.** `TrustedHostPromotion ∪ AuthoritativeCIPromotion`, validadores
  disjuntos, **nunca** unidas por `check_name`. Um check verde do GitHub chamado `pytest` não é
  evidência sobre um resultado advisory chamado `pytest`.
- **Autoridade é derivada, nunca declarada.** Não existe parâmetro `authoritative=True`.
  `authority_effect="promotable"` é *saída* das verificações, não entrada.
- **Binding 1:1 por digest, nos dois sentidos.** O digest é a *chave de junção*; cada par também
  precisa concordar em `run_id`, `head_sha`, `repository`, `base_sha`, `tested_merge_sha` e
  `check_name`.
- **HEAD ≠ árvore testada.** `RequiredCheckResultV2.head_sha` mantém o sentido congelado; a prova
  da árvore executada vive no sidecar, com parentage `[base, head]` sensível à ordem.
- **Regra por origem.** `pull_request_target`/`manual`/`replay` nunca herdam semântica de merge
  sintético; origem não declarada na política é `origin_unsupported`.
- **Uma cadeia só.** `acquire → snapshot canônico → verifier/assembler`. Nenhuma resposta de API
  em memória alcança o assembler.

### Contratos

Congelados e **intocados**: `RequiredCheckResultV2`, `ReviewReadinessV2`, `TrustedCheckPlanV2`,
`TrustedCheckResultV2`, `TrustedCheckOutcomeV2`, `TrustedCheckAuthorityV2`,
`AllowlistedCheckCommandV2`, `RunIdentityV2`, `RunOriginV2`, `AgentReviewRunV2`,
`TargetProfileV2`. Preimages de `result_sha256`/`artifact_sha256`/`profile_hash`/`compute_run_id`
inalteradas. Os 15 schemas pré-existentes permanecem byte-idênticos.

Aditivos (3 novos schemas exportados): `agent-review.required-check-provenance.v2`,
`agent-review.authoritative-check-policy.v2`, `agent-review.authoritative-check-snapshot.v2`.

### Não-mudanças deliberadas

- `trusted_checks_v2._canonical_json_bytes_v2` e `isolated_executor_v2._canonical_json_bytes_v2`
  **não** foram unificados — moveria `result_sha256`/`artifact_sha256`.
- O parsing estrito de `trusted_check_supervisor_v2` (B3, adversarialmente pinado) e de
  `app/ri_b0a/reuse_manifest.py` (trilha RI) **não** foi consolidado.
- `review_readiness_emission_v2.py` e `readiness_decision_v2.py` **não** foram tocados.

## Limitação conhecida — `workflow_ref` e workflows base-owned

A API de Actions do GitHub reporta o `path` de um workflow run, mas **nenhum campo** afirma de
qual ref a **definição** do workflow foi carregada. Em eventos `pull_request`, o GitHub executa o
arquivo de workflow **como ele existe no merge commit da própria PR** — ou seja, uma PR pode
modificar o workflow que produz seus próprios checks.

Portanto **a ameaça `C0-T4` não é fechada por metadado do GitHub isoladamente.**

O acquirer registra o que de fato aconteceu (`refs/pull/<n>/merge`) e nunca afirma uma origem
base-owned que não observou. A consequência é deliberada e visível: uma política que fixa
`workflow_ref` no default branch **não casa** com um run disparado por `pull_request`, e nada é
promovido. Falhar fechado e ruidosamente é o comportamento correto — a alternativa seria uma
política que aparenta provar base-ownership sem provar nada. Um teste fixa esse comportamento
para que não seja silenciosamente "corrigido" fabricando a garantia.

**Fechar `C0-T4` de verdade é decisão de configuração do target**, não algo que este código possa
simular: o produtor autoritativo precisa ser disparado de forma que rode a definição base-owned
(por exemplo um job disparado por `workflow_run`, um reusable workflow pinado pela base, ou branch
protection + CODEOWNERS sobre o path do workflow). Registrado aqui, **não resolvido**.

Consequência prática: a política shipada nas fixtures é estruturalmente correta e testada, mas um
target real só consegue promover pytest depois dessa decisão. Isso **não** bloqueia a `#201-C0`
como fronteira de autoridade — bloqueia a ativação operacional, que já dependia de `#201-C` e do
CT104.

## Gates executados

| Gate | Resultado |
|---|---|
| testes focados C0-1…C0-6 | verde |
| regressão offline completa | 2120 passed, 4 skipped |
| suíte `requires_network` | verde |
| `bash scripts/ci_validate.sh` (seções 1–8) | **OK** |
| `export-agent-review-v2-schemas.py --check` | byte-idêntico |
| `verify-caem-f0-pin.py --check` | verde |
| API GitHub real | **não executado** — fixtures gravadas; não é gate de merge da C0 |
| CT104 | `blocked_external: ct104_unavailable` |

Testes verificados como genuínos por mutação: relaxar a ordem dos pais, remover a ambiguidade de
tentativa e reintroduzir match por nome no verificador falham exatamente o teste escrito para cada
um, e voltam a passar no restore.

## Estado vetorial

```
#201-B3_IMPLEMENTATION=MERGED
#201-B3_OPERATIONAL_CLOSURE=BLOCKED_BY_CT104
#201-C0_IMPLEMENTATION=READY_FOR_REVIEW
#201-C0_C0T4_BASE_OWNED_WORKFLOW=UNRESOLVED_TARGET_CONFIGURATION
#201-C=BLOCKED
#217=OPEN (cobre-se o caminho exercido pela #201; a classe completa permanece)
#201=OPEN
CT104_CONFORMANCE=gate_unavailable
CAPABILITY_ACTIVATION=BLOCKED_UNTIL_CT104_PASS
MERGE_WITHHELD_BY_AUTHORITY
```

## Próxima ação mínima

Revisão da PR. Depois, em ordem: (1) decidir a configuração base-owned do produtor autoritativo
(limitação `C0-T4` acima) — decisão de target/`#203`, não de código deste repositório;
(2) `#201-C` (wiring em `ReviewReadinessV2`), que permanece bloqueada pelo fechamento operacional
da `#201-B3` no CT104. Merge, tag, release, deploy, repin, ativação de capacidade e fechamento de
`#201` seguem **retidos**, cada um exigindo grant nominal próprio.
