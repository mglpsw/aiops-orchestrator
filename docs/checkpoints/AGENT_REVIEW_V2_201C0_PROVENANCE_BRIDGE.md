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

## Rodada 1 de review adversarial (Codex) — três achados, todos válidos

**Achado 1 (P1) — sidecar bem-formado não é evidência.** A primeira versão do gate verificava
*estrutura*: binding 1:1, identidade de run, conformidade com a política. Todos esses campos são
deriváveis de entradas públicas — a identidade está no arquivo de identity, os digests da política
são computáveis do checkout base, e os campos de produtor estão escritos na própria política. Logo,
quem pudesse escrever `--checks` e `--checks-provenance` conseguia forjar um verde completo com
sidecar internamente consistente. A própria fixture de teste demonstrava o ataque.

Correção: o gate **não confia mais na submissão**. `reassemble_and_verify_required_checks_v2`
re-executa o assembler sobre `--checks-snapshot` e só aceita o par se ele for exatamente o que o
assembler produz de forma independente. A submissão vira uma alegação conferida contra evidência
derivada, não evidência em si. Novos argumentos obrigatórios: `--checks-snapshot`, `--run-origin`,
`--toolchain-digest`.

Isso não transforma um *snapshot* forjado em evidência — essa é a fronteira de confiança do
acquirer, host-owned por construção. O que elimina é o ataque estritamente mais fácil: pular a
aquisição inteira.

**Achado 2 (P1) — parentage local não prova qual merge rodou.** Um check run é escopado a um HEAD,
mas o que ele executou é um merge daquele head com uma base — e a base pode avançar sem o head se
mover. Um verde produzido contra a base anterior, somado a um merge commit recém-criado cuja
parentage confere, passava. Correção: a observação agora registra `run_base_sha`/`run_head_sha`
como o GitHub os reportou, e o assembler exige que casem com a identidade. Base divergente ⇒
`observation_stale`.

**Achado 3 (P2) — `pull_request_target` estava sendo mapeado errado.** Esse evento carrega o
workflow da branch **base** — é a sua propriedade definidora. Registrar um pull ref para ele era
factualmente errado e tornava todo run `pull_request_target` permanentemente não-autorizável.
Correção: o ref vem de `pull_requests[].base.ref`. Isso também torna `pull_request_target` **uma
das formas legítimas** de um target satisfazer a exigência de produtor base-owned.

Eventos de trigger não reconhecidos agora são recusados na aquisição em vez de virarem um valor
genérico que o código adiante teria de adivinhar.

## Rodada 2 de review adversarial (Codex) — dois achados, ambos válidos

**Achado A (P1) — `executed_tree_sha` era uma tautologia.** O acquirer só conseguia copiar o
próprio `--tested-merge-sha` do chamador para esse campo, e o assembler então comparava
`snapshot.executed_tree_sha == identity.tested_merge_sha` — isto é, comparava a entrada do chamador
consigo mesma. Uma checagem vazia vestida de prova.

Pior: a correção P2 da rodada 1 tornou `pull_request_target` autorizável, e esse evento **faz
checkout da base por padrão, não do merge**. Logo um run verde de `pull_request_target` poderia
carregar metadados de base/head corretos e ainda assim ser rotulado como tendo executado o merge.
A correção da rodada 1 abriu exatamente este buraco.

Correção: o campo `executed_tree_sha` foi **removido** do contrato — um campo que ninguém observa
convida falsa confiança. O que de fato liga o run a este merge é o `run_base_sha`/`run_head_sha`
do próprio run, que o GitHub reporta. E `explicit_tested_tree` passa a ser **recusado no load da
política**, com mensagem própria, para que um target descubra que sua política não funciona ao
escrevê-la — não quando uma review silenciosamente nunca fica ready.

**Consequência honesta:** hoje **só `pull_request` é promovível**. `pull_request_target`, `manual`
e `replay` não têm evidência de árvore executada que o C0 possa verificar, e são recusados. Isso
corrige uma afirmação otimista demais que eu havia feito ao fechar a rodada 1.

**Achado B (P2) — escopo da query para `pull_request_target`.** Válido: runs desse evento são
associados ao commit da base e não seriam recuperados por uma query escopada ao head. Como esse
caminho deixou de ser promovível (achado A), a query escopada ao head é **coerente**, não uma
lacuna — não recuperar esses runs não muda veredito nenhum. Adicionar uma query por base coletaria
evidência para um caminho que falha fechado de qualquer forma e sugeriria que ele funciona.
Registrado no docstring do acquirer: se um produtor futuro tornar `pull_request_target`
verificável, o escopo da query deve ser revisto **junto com** essa mudança, não antes dela.

## Rodada 3 de review adversarial (Codex) — dois achados P1, ambos válidos

**Achado A — evento observado não estava ligado à origem declarada.** `select_observation_v2`
nunca verificava `observation.run_event == origin.event_type`. Um chamador podia declarar
`pull_request` (ganhando a regra de merge sintético) e promover um run `pull_request_target`, que
executa a **base**, não o merge.

Isto era pior do que o achado descreve, e vale registrar o mecanismo: a política exige
`workflow_ref` no default branch; runs `pull_request` genuínos registram um **pull ref**, enquanto
runs `pull_request_target` registram o **default branch**. Ou seja, as únicas observações capazes
de satisfazer a política hoje eram exatamente as base-executed. Declarar `pull_request` como origem
lhes concedia a semântica de merge sintético.

Correção: `required_check_provenance_origin_event_mismatch`, aplicado **antes** de qualquer
semântica dependente de origem.

**Achado B — `run_attempt` não ordena runs distintos.** `run_attempt` conta dentro de um único
`workflow_run_id`. Tomar o máximo entre runs identifica o maior **número** de tentativa, não a
execução mais recente: um run antigo re-executado até a tentativa 3 vencia um run novo na tentativa
1 — selecionando um verde obsoleto sobre um vermelho atual, exatamente a falha que essa seleção
existe para impedir.

Correção: a observação passa a registrar `run_started_at`, e a seleção ordena por
`(run_started_at, run_attempt)` — run mais recente primeiro, depois a tentativa mais recente dele.
Empate nesse par continua sendo ambiguidade recusada. O acquirer **recusa** um run sem timestamp:
adivinhar ordenação é como um verde obsoleto vence um vermelho atual.

## Rodada 4 de review adversarial (Codex) — a ponte não promovia nada

**Achado (P1) — nenhuma configuração de target era expressível.** A política exigia
`workflow_ref == refs/heads/<default>` como prova de base-ownership; runs `pull_request` genuínos
executam sob `refs/pull/<n>/merge`; `pull_request_target` passou a ser recusado na rodada 2. Logo
**nenhuma observação real podia ser promovida**, e as fixtures só passavam porque combinavam
`run_event=pull_request` com `workflow_ref=refs/heads/master` — estado que o GitHub nunca emite.

Isso é mais forte do que a limitação registrada antes: não era "configuração de target ainda não
feita", era "nenhuma configuração de target seria expressível", porque `RunOriginV2` está
congelado e não admite um trigger de produtor base-owned.

**Resolução ratificada.** `RunOriginV2` permanece **intocado** — é o contrato congelado da origem
da *revisão*, não do trigger interno do produtor. Foi adicionado um modelo aditivo separado,
`authoritative_producer_evidence_v2`, que separa cinco fatos antes colapsados em uma string:

```text
review_origin           o que a REVISÃO é          RunOriginV2 (congelado)
producer_trigger        como o PRODUTOR disparou   observado, nunca inferido
workflow_execution_ref  ref sob o qual rodou       observação factual, prova nada
producer workflow       path @ SHA de 40 chars     identidade imutável
executed-tree evidence  attestation                prova, não inferência
```

Primeiro `producer_kind` ratificado: **`sha_pinned_reusable_workflow`**. A autoridade vem do
reusable workflow pinado por SHA completo — o GitHub registra `referenced_workflows` com o commit
SHA do workflow que carregou, e uma PR pode editar sua própria árvore mas não pode fazer um run
referenciar um SHA que ele não carregou.

A árvore executada vem de uma **attestation** emitida por um job do produtor **sem checkout e sem
executar código da PR** — caso contrário o sujeito estaria atestando a própria execução, que é a
fronteira da `#201-B3` violada em outro lugar. O assembler exige
`attested_executed_sha == identity.tested_merge_sha`, e recusa se produtor e GitHub discordarem
sobre o resultado.

`workflow_ref` **deixou de ser prova de base-ownership**. É registrado como
`workflow_execution_ref`, observação factual, e não participa da correspondência de produtor.

Um teste fixa a inversão: **um pull ref agora é o caminho promovível normal**, e um workflow
referenciado em SHA diferente do pinado é recusado.

## Rodada 5 de review adversarial (Codex) — a aquisição real nunca buscava attestations

**Achado (P1) — a rodada 4 fechou o modelo e deixou o caminho vivo aberto.** `_fetch_payload`
montava `check_runs` e `workflow_runs`, e nunca `producer_attestations`. No caminho vivo toda
observação saía com `producer_attestation=None`, e o assembler — corretamente — recusava cada
required check com `..._producer_attestation_missing`. Só o caminho de fixture gravada promovia
qualquer coisa.

**É a mesma classe de adiamento que a rodada 4 rejeitou**, cometida uma rodada depois: eu havia
escrito que buscar attestations "é uma preocupação separada de decidir se elas são válidas". Sob a
decisão ratificada na rodada 4, um modelo capaz de representar um produtor real precisa também
conseguir *observá-lo*; caso contrário a ponte volta a promover nada.

**Resolução.** O acquirer passou a buscar as attestations dos artifacts do run do produtor:

- `extract_attestation_from_zip_v2()` — limites em todos os eixos que um zip pode ser hostil
  (tamanho do zip, número de entradas, tamanho do membro), lê **apenas** o membro esperado
  (`attestation.json`). Ler "o único arquivo que estiver dentro" deixaria o atacante escolher o
  payload escolhendo o nome do arquivo. Parse estrito, objeto obrigatório, `AcquisitionError`
  fail-closed com `authoritative_check_attestation_artifact_invalid`;
- `collect_attestations_v2(*, workflow_runs, list_artifacts, download_artifact)` — a lógica de
  binding recebe as chamadas injetadas, então é testável sem token e sem rede; artifact expirado
  ou com outro nome é ignorado, nunca substituído por um default;
- `_fetch_payload` vivo passou a listar `/actions/runs/{run_id}/artifacts` e baixar
  `/actions/artifacts/{id}/zip`.

O artifact continua sendo **entrada não confiável**: buscá-lo não o valida. Quem decide se a
attestation vale é o assembler (`verify_producer_attestation_v2`), inalterado nesta rodada.

Nove testes novos; `extract_attestation_from_zip_v2` verificado por mutação — trocar o match de
nome de membro por "primeira entrada do zip" falha exatamente `test_only_the_expected_member_is_read`.
As asserções desses testes afirmam o tipo de exceção e o reason code, não `Exception` genérica: um
`pytest.raises(Exception)` passaria também com um erro de digitação no próprio teste, que é
precisamente a classe de asserção vazia que esta PR existe para manter fora do caminho de
proveniência.

## Rodada 6 de review adversarial (Codex) — a ordenação empatava entre runs

**Achado 1 (P1) — comparação de tentativas entre workflow runs distintos.** A correção da rodada 3
trocou "maior `run_attempt`" por um máximo sobre o par `(run_started_at, run_attempt)`, e a
docstring passou a afirmar "o run mais recente vence, depois a última tentativa desse run". O
código não fazia isso. `run_started_at` tem precisão de segundo, então dois workflow runs
**distintos** podem empatar nele; nesse caso a comparação de tupla cai para `run_attempt`, que só
tem significado dentro de um mesmo `workflow_run_id`. Um run antigo re-executado até a tentativa 3
(verde) e um run novo na tentativa 1 (vermelho) iniciados no mesmo segundo selecionavam o **verde
obsoleto** — exatamente a falha que essa seleção existe para impedir, um empate de distância.

É a mesma categoria de erro da rodada 3, sobrevivendo dentro da própria correção da rodada 3.

**Correção.** Dois passos separados, nessa ordem: (1) maior `run_started_at`; se mais de um
`workflow_run_id` distinto empata nele, **recusa** — nada nos dados ordena os dois runs, e escolher
qualquer um seria um palpite vestido de decisão; (2) dentro do run único vencedor, maior
`run_attempt`; dois registros no mesmo run e mesma tentativa são contradição, não escolha.

**Achado 2 (P2) — aquisição viva lia só a primeira página.** Os endpoints de lista do GitHub
paginam (30 por padrão, 100 no máximo). `check-runs`, `actions/runs` e `artifacts` liam página 1 e
descartavam o resto em silêncio, então um produtor que **rodou** era reportado como ausente sempre
que o HEAD tivesse mais runs do que cabe numa página. "Ausente" aqui não é resultado neutro: é
evidência sobre a qual o gate age.

**Correção.** `paginate_envelope_v2(*, get_json, path, key)` — envelope-aware, `per_page=100`,
`get_json` injetado (testável sem token e sem rede), e **recusa em vez de truncar** ao esgotar
`MAX_ACQUISITION_PAGES`. Uma lista curta em silêncio é indistinguível de "o produtor não rodou", e
uma dessas duas coisas é mentira. A chave do envelope ausente também recusa: o antigo
`.get(key, [])` transformava payload alterado ou com erro em "nenhum run".

**Threads antigas ainda abertas, verificadas.** As três threads não-outdated remanescentes
(`required_check_assembly_v2.py:213` e `:273`, `aiops-acquire-authoritative-checks-v2.py:355`) já
estavam corrigidas nas rodadas 3 e 4; o GitHub as ancora em linhas cujo conteúdo mudou. Duas
**prosas** ficaram desatualizadas depois que a rodada 4 removeu `workflow_ref` do casamento de
produtor, e foram corrigidas aqui — comentário que descreve errado o próprio código é exatamente o
tipo de afirmação não verificada que esta slice existe para não produzir.

## Rodada 7 de review adversarial (Codex) — o artefato não estava ligado ao produtor

**Achado 1 (P1) — a attestation era aceita por convenção de nome.** O modelo da rodada 4 pinava um
reusable workflow por SHA completo e tratava isso como identidade do produtor. O pin prova que o
run **carregou** aquele workflow — e nada além disso. Dentro de um run `pull_request` a própria PR
pode chamar o workflow pinado **e** acrescentar um job seu que publica
`aiops-authoritative-check-attestation`, preenchendo repository, pr_number, base, head,
executed_sha, run id e attempt: todos valores que ela já conhece.

Verificar esses campos, portanto, provava apenas que o forjador foi cuidadoso. A fronteira correta
não é uma checagem melhor sobre a mensagem; é **recusar mensagens vindas de dentro de um run em que
a PR pode escrever**.

**Achado 2 (P2) — o acquirer não tinha guarda de colisão output/input.** Com `--observations` e
`--output` no mesmo caminho resolvido, ele lia o payload gravado, sobrescrevia com o snapshot
derivado e retornava sucesso. Isso destrói a evidência bruta do GitHub de que uma auditoria ou
reexecução depende, e um snapshot que comeu a própria fonte não pode ser re-derivado nem conferido.
O quality gate já recusa essa classe duas vezes (`#145`, `#156`).

### Decisão ratificada

`fail closed now, sign later` — **com a condição obrigatória de que a C0 não termine apenas
desligando `pull_request`**. Esta rodada implementa e prova um primeiro producer kind unsigned que
é efetivamente base-owned:

```text
producer_kind    = base_owned_workflow_run
producer_trigger = workflow_run
```

A diferença em relação à rodada 4 é arquitetural, não cosmética:

| Rodada 4 | Depois da rodada 7 |
|---|---|
| não existia produtor real representável | existe `base_owned_workflow_run` representável |
| `#203` não podia configurar nada que o modelo aceitasse | `#203` pode instalar a topologia já suportada |
| C0 promovia zero **por defeito de modelo** | C0 promove zero nos targets atuais **por falta de adoção** |

### O que passou a valer

```text
pull_request  + artefato convencional por nome        → NÃO PROMOVÍVEL
pull_request  + attestation autenticada por assinatura → caminho futuro, não implementado
merge_group                                            → inelegível sem modelo próprio
workflow_run base-owned + attestation do próprio run   → PROMOVÍVEL
```

Bindings exigidos para o produtor base-owned: `producer_trigger == workflow_run`;
`workflow_repository`, `workflow_path`, `workflow_sha` e `workflow_execution_ref` iguais aos
pinados na política; `job_name` igual; e a attestation ligada a run id, attempt, repository,
pr_number, base, head e `executed_sha == identity.tested_merge_sha`.

Duas declarações novas na attestation, porque ser base-owned torna o produtor confiável sobre o que
**ele** fez, não sobre o que o run da PR fez:

- `check_execution_mode` — republicar um artifact do run da PR não é reexecução. A orientação do
  próprio GitHub é que artefatos de um workflow que processou código não confiável são dados não
  confiáveis; encaminhar um sem verificar é lavagem, não verificação;
- `executed_sha_derivation` — a árvore executada precisa vir do checkout verificado do produtor,
  não de um input repetido. Um valor que andou em círculo não é evidência, por mais confiável que
  seja o mensageiro.

`sha_pinned_reusable_workflow` continua **declarável** e é recusado **no load da política**, com a
mesma disciplina de `explicit_tested_tree`: o target descobre que seu produtor não funciona ao
escrever a política, não quando uma review silenciosamente nunca fica ready.

`RunOriginV2` permanece intocado. Origem da revisão (`pull_request`) e trigger do produtor
(`workflow_run`) agora **legitimamente diferem** — é exatamente a separação que o modelo de producer
evidence existe para representar, e colapsá-las de novo tornaria o único produtor sólido
inexprimível.

### Consequência operacional a decidir em `#203`

A política pina `producer_workflow.sha` como SHA completo. Para um produtor `workflow_run` o SHA
observado é o topo do default branch, logo **o pin precisa ser rotacionado quando o commit do
produtor muda**. Isso é propriedade da instalação, não do engine: `#203` deve escolher a estratégia
de rotação. Registrado aqui como consequência conhecida, não como defeito silencioso.

## Auditoria independente de `8b7e94c`, e retificação arquitetural

Com o CI real verde em `8b7e94c` e a quota do Codex esgotada, uma auditoria independente
read-only (três agentes, cada um em superfícies distintas: producer ownership/artifact binding,
executed-tree identity/digest binding/gate fail-closed, run-attempt ordering/pagination) revisou o
mesmo HEAD. Cada achado citado abaixo foi verificado por mim antes de ser aceito — reproduzido em
código, não apenas relatado.

**4 P1 confirmados, 8 P2 confirmados.** Dois P2 (security) foram corrigidos imediatamente, red-first,
em commits próprios, independentes de qualquer decisão arquitetural:

- o token do GitHub era repassado ao host de blob storage no redirect do download de artifact
  (`urllib` não remove `Authorization` por padrão; só `Content-Length`/`Content-Type` são
  removidos) — `_StripAuthOnRedirectHandler` remove o header incondicionalmente em todo redirect;
- `str(check.get("id"))`/`str(run.get("id"))` produziam a string `"None"` quando o campo faltava, e
  `"None"` satisfaz `SafeIdentifier` — dois runs distintos sem `id` colapsavam na mesma identidade
  fabricada, permitindo que um verde obsoleto (rerun até attempt 3) vencesse um vermelho atual
  (attempt 1) silenciosamente. Reproduzido e corrigido com `_require_id`, que recusa em vez de
  fabricar, como todo outro campo de identidade neste módulo já fazia.

Mais cinco P2 mecânicos, independentes da arquitetura, corrigidos no mesmo padrão red-first:
ambiguidade de membro de zip (`attestation.json` duplicado — mesma classe já recusada um nível
acima para artifacts duplicados), `run_attempt` fabricado via `or 1`, `runs_by_suite` como
dict-comprehension com colisão silenciosa de `check_suite_id`, blast radius de DoS via artifact em
run PR-writable (agora só runs `workflow_run` são oferecidos a `collect_attestations_v2`), e uma PR
de fork podendo envenenar o snapshot inteiro (GitHub deixa `pull_requests` vazio para runs
cross-repo; a checagem moveu de parse-time — recusa o arquivo todo — para o assembler, per-check).
Também: `verify_required_check_provenance_set_v2(loaded_policy=None)` era fail-open por default;
nenhum chamador real dependia disso, e o parâmetro passou a ser obrigatório.

### Os dois P1 arquiteturais

**P1 — o positive path da rodada 7 não funciona com dados reais.** O acquirer escopa as duas
queries por `?head_sha={PR head}` (filtro server-side), e grava `workflow_sha = run["head_sha"]`.
Para qualquer run retornado por essa query, `workflow_sha` é necessariamente o HEAD da PR — nunca
o SHA base-owned que a política pina. A fixture positiva load-bearing da rodada 7 usava
`workflow_sha=PRODUCER_WORKFLOW_SHA` junto de `head_sha=HEAD`, uma combinação que o acquirer nunca
poderia produzir. É a mesma classe de defeito da rodada 4 (fixture codificando um estado do GitHub
impossível), reintroduzida uma camada abaixo, por mim, na correção que deveria tê-la fechado.

**P1 — a arquitetura do produtor contradiz a si mesma.** `verify_producer_is_base_owned_v2`
depende de "a PR não pode escrever neste run"; três funções abaixo,
`verify_producer_execution_is_first_hand_v2` **exige** `check_execution_mode ==
"reexecuted_in_producer_run"` — ou seja, exige que o produtor reexecute a suíte de testes **da
própria PR** e reporte o exit code dela. O `#201-B3` ratificou:

```text
controls(subject, success_signal)  ⇒  not authoritative(success_signal)
```

Um workflow base-owned na **definição** não muda quem determina o `success_signal`: o próprio
código de teste da PR ainda decide se `pytest` sai com 0 ou 1. Mover essa execução do executor
isolado para um `workflow_run` base-owned realoca a fronteira da B3; não a atravessa.

### Decisão ratificada

Corrigir os dois P1 com mais patches locais no modelo atual foi explicitamente descartado. Em vez
disso:

1. **A condição de aceite da rodada 7** — "C0 sai com um positive path base-owned funcionando para
   pytest" — está **revogada**, não reinterpretada. Nova evidência mostrou que ela força uma
   arquitetura incoerente.
2. **O teorema da B3 é preservado por inteiro.** Nenhum `check_execution_mode` hoje fornece um
   julgamento semântico independente do subject — `reexecuted_in_producer_run` reexecuta o teste
   da própria PR; `upstream_artifact_republished` nem reexecuta, apenas encaminha. Nenhum dos dois
   qualifica.
3. `AuthoritativeCIPromotion` passa a ser recusada **incondicionalmente**, por um único gate final
   e explícito — `verify_independent_semantic_judge_v2`, chamado por último em
   `assemble_authoritative_ci_promotion_v2`, depois que identidade de produtor, base-ownership e
   tree binding já passaram. Isso preserva essa infraestrutura como real e testada (ela ainda fecha
   o bypass da `#217` — binding por digest, nunca por nome) e ao mesmo tempo torna impossível
   qualquer coisa que hoje aparentasse `AuthoritativeCIPromotion(pytest)`.
4. A correção de query-scope do acquirer (âncorar por um `producer_run_id` seletor não confiável em
   vez de `head_sha`/`tested_merge_sha`) fica **explicitamente adiada** para uma emenda C0
   ratificada em conjunto com a instalação do produtor em `#203` — não implementada nesta rodada.
   Documentada como limitação conhecida no docstring do acquirer, não escondida.

Trinta e dois testes do gate CLI e da suíte de assembly dependiam de `state: ready` ser alcançável
via `pytest` CI-sourced — o único caminho que já foi promovível neste ambiente
(`trusted_host_promotion` é recusado na re-derivação do gate incondicionalmente, por constatação
pré-existente e não relacionada: CT104 está offline). Doze testes cuja fixture-construction
dependia do assembler foram religados via um bypass de teste explícito, escopado, documentado
(`_ci_promotion_bypassing_independent_judge_gate`), que nunca alcança o processo real do gate.
Nove testes do CLI cujo ÚNICO cenário de sucesso passava por essa promoção foram reescritos para
provar a propriedade que ainda é real: a submissão atravessa toda checagem anterior e é recusada
apenas no gate terminal — não por colisão de output/input, não por mismatch de contrato, não por
cobertura incompleta. Dois testes cuja asserção original (`state: ready`) tornou-se literalmente
falsa foram deletados por estarem substituídos pelos novos testes que afirmam o oposto. Onde a
cobertura de um mecanismo específico (decision-provenance replay) já existia de forma independente
em `test_review_readiness_emission_v2.py` — arquivo que a `#201-C0` é proibida de tocar — o teste
do CLI foi reescrito citando essa cobertura em vez de duplicá-la.

Estado honesto resultante:

```text
#201-C0_SECURITY_BOUNDARY=IMPLEMENTED
#217_EXERCISED_BYPASS=CLOSED
AUTHORITATIVE_PYTEST_PROMOTION=UNAVAILABLE_BY_DESIGN
INDEPENDENT_SEMANTIC_JUDGE=REQUIRED
ACQUIRER_QUERY_SCOPE=KNOWN_LIMITATION_DEFERRED_TO_C0_AMENDMENT
```

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
| regressão offline completa | 2211 passed, 4 skipped |
| suíte `requires_network` | verde |
| `bash scripts/ci_validate.sh` (seções 1–8) | **OK** |
| `export-agent-review-v2-schemas.py --check` | byte-idêntico |
| `verify-caem-f0-pin.py --check` | verde |
| auditoria independente read-only (3 agentes, superfícies distintas) | executada; achados verificados antes de aceitos |
| API GitHub real | **não executado** — fixtures gravadas; não é gate de merge da C0 |
| CT104 | `blocked_external: ct104_unavailable` |

Testes verificados como genuínos por mutação: relaxar a ordem dos pais, remover a ambiguidade de
tentativa, reintroduzir match por nome no verificador, trocar o membro esperado do zip de
attestation por "primeira entrada", remover a recusa de runs empatados no horário de início, parar
a paginação após a primeira página, desligar cada uma das três guardas da rodada 7 (produtor
PR-writable, artefato upstream republicado, executed_sha vindo do chamador), remover o header
`Authorization` do redirect, restaurar a fabricação de identidade a partir de `id` ausente, e
remover o gate `verify_independent_semantic_judge_v2` — tanto no nível de unidade quanto ponta a
ponta via o CLI do gate real — falham exatamente o teste escrito para cada um, e voltam a passar no
restore.

## Estado vetorial

```
#201-B3_IMPLEMENTATION=MERGED
#201-B3_OPERATIONAL_CLOSURE=BLOCKED_BY_CT104
#201-C0_IMPLEMENTATION=READY_FOR_REVIEW
#201-C0_INDEPENDENT_AUDIT=COMPLETE_4_P1_8_P2_CONFIRMED
#201-C0_SECURITY_BOUNDARY=IMPLEMENTED
#217_EXERCISED_BYPASS=CLOSED
AUTHORITATIVE_PYTEST_PROMOTION=UNAVAILABLE_BY_DESIGN
INDEPENDENT_SEMANTIC_JUDGE=REQUIRED
ACQUIRER_QUERY_SCOPE=KNOWN_LIMITATION_DEFERRED_TO_C0_AMENDMENT
UNSIGNED_PULL_REQUEST_PRODUCER=INELIGIBLE
CRYPTOGRAPHIC_PRODUCER_MODE=DEFERRED
TARGET_INSTALLATION_OWNER=#203
#201-C=BLOCKED
#217=OPEN (cobre-se o caminho exercido pela #201; a classe completa permanece)
#201=OPEN
CT104_CONFORMANCE=gate_unavailable
CAPABILITY_ACTIVATION=BLOCKED_UNTIL_CT104_PASS
MERGE_WITHHELD_BY_AUTHORITY
```

## Próxima ação mínima

Revisão da PR (Codex round 8, quando a quota do provedor voltar; `NOT_RUN_GATE_UNAVAILABLE` até lá,
nunca `PENDING_FINDINGS=0` — ausência de rodada não é evidência negativa). Merge segue **NO-GO**:
`8b7e94c` tinha CI real verde mas não satisfazia o critério load-bearing da rodada 7, que a
auditoria demonstrou ser falso; o HEAD atual corrige os achados confirmados e revoga essa condição
explicitamente em vez de forçá-la.

Depois, em ordem, cada um exigindo grant nominal próprio e nenhum iniciável antes do fechamento
operacional anterior: (1) uma emenda C0 ratificada cobrindo tanto a correção de query-scope do
acquirer (`producer_run_id` como seletor não confiável, nunca `head_sha`/`tested_merge_sha`) quanto
a decisão de instalação do produtor base-owned em `#203` — as duas juntas, não uma sem a outra,
porque a segunda sem a primeira reproduziria exatamente o P1 desta rodada; (2) desenho de um
producer kind com julgamento semântico genuinamente independente do subject, sem o qual
`AuthoritativeCIPromotion(pytest)` permanece `UNAVAILABLE_BY_DESIGN` por tempo indefinido — `#203`
pode instalar a topologia, mas instalação sozinha não cria autoridade; (3) `#201-C` (wiring em
`ReviewReadinessV2`), que também permanece bloqueada pelo fechamento operacional da `#201-B3` no
CT104. Merge, tag, release, deploy, repin, ativação de capacidade e fechamento de `#201`/`#217`
seguem **retidos**.
