# #301 S0 — contrato proposto do subject de execução autenticado (S) e da fronteira de confiança

```yaml
status: S0_S_G_RATIFIED_AS_COMPONENT_CONTRACT     # S_D and E remain PROPOSED; nothing is implemented in production
architecture_C_status: RATIFIED_AS_COMPONENT_CONTRACT   # maintainer ratification; applicability requires AuthorizedReaderExecutionContext
experimental_subject: 34fc57562edfcb8f59d3ed0c359dc9bffb95d47d   # 216/216 there; the ratification commit changes documentation only
architecture: C_PRIVILEGE_SEPARATED_IMMUTABLE_SNAPSHOT   # ratified component contract; A and B are REJECTED PREDECESSORS
claim_scope: S_G_only
slice: S0_contract_and_experimental_evidence   # nome de trabalho; não é claim normativa, milestone nem revisão CAEM
work_owner: "#301"          # roadmap: #46; núcleo: #80
base: 9abcde6420a59b814b5faaff10ca5904c5d23370   # tree 93143d70ed410771776f5f2cdb48d7f8e3f5ed9b
production_S_implemented: false
production_E_implemented: false
production_implementation: none   # architecture C exists only as experiments/ prototypes
evidence_index: EVIDENCE.md
experiments: experiments/            # opt-in; fora de app/, scripts/ e da descoberta do pytest
```

### Decisão de corte do mantenedor (2026-09-27, após STOP/REDESIGN da rodada 2)

```yaml
S0_decision:
  S_G:  {contract: keep_and_finish, status_target: ready_for_adjudication}
  S_D:
    architecture: preserve_as_proposed
    proof_claims: {resource_boundedness: future_S_D_slice, native_closure: future_S_D_slice,
                   loader_completeness: future_S_D_slice}
    mandatory_countermodels: [R2-1, R2-2, R2-3, R2-7, R2-8]   # PR #355, comentário 5852251027
  E: {remains_future: true}
```

### Decisões do mantenedor após o STOP/REDESIGN da rodada 3 (2026-09-27)

```yaml
a_git_transport:   {decision: KEEP_GIT_BUT_CONTAIN_THE_CHILD, own_git_pack_reader: rejected,
                    mechanism: [hardened git executable/env, memory rlimit applied before exec,
                                strict bounded header, kill/reap on refusal],
                    RLIMIT_DATA_spike: feasibility_only, S1_limit: RLIMIT_AS (focal validation required)}
b_acquisition:     {current_C3_spool_as_truth_maker: rejected, second_structural_parser: rejected,
                    selected: [one bounded acquisition path, same bytes authenticated AND interpreted,
                               C3 remains tree-semantic authority, seal then revalidate the final S_G],
                    invariant: VerifiedRepresentation == ConsumedRepresentation}
c_sha1:            {supported: true, assumption: second_preimage_resistance_for_fixed_trusted_oid,
                    sha1dc_equivalence_claimed: false, sha256_supported: true}
d_component_limit: {hardcoded_255: rejected, independent_fpathconf_in_S1: rejected,
                    selected: explicit_admission_parameter, C3_parity: conditional_on_same_limit}
```

### Arquitetura de aquisição: histórico e contrato ratificado (2026-09-27)

```yaml
architecture:
  A: {name: LIVE_GIT_TRANSPORT, rounds: [R1, R2 STOP, R3 STOP, R4 terminal recurrence], status: REJECTED}
     # PR #355 comments 5852251027, 5852389024, 5852664959
  B: {name: MUTABLE_PRIVATE_SNAPSHOT, evidence: [Spike B 5852938161, candidate head 8c4842b],
      review: "B-1..B-4 (comment 5853311835)", status: REJECTED}
  C: {name: PRIVILEGE_SEPARATED_IMMUTABLE_SNAPSHOT, evidence: ["Spike C 22/22 (comment 5858608157)", "34fc575 216/216"],
      status: RATIFIED_AS_COMPONENT_CONTRACT}   # a successor architecture, not a later round of A or B
```

A e B **não** foram consertadas: foram rejeitadas, e C é outra fronteira. A evidência de A/B não se
transfere para C sem reexecução; o que C reutiliza (hash-on-read, parse estrito, selo e
revalidação, orçamentos da closure) foi reexecutado na execução registrada do head experimental
`34fc575`.

**Arquitetura C (contrato de componente ratificado).** Dada uma capability de storage admitida e um produtor com separação
de privilégio em relação ao runner, o produtor publica atomicamente um snapshot físico autocontido
que o runner pode ler e não pode alterar. S_G é derivado de um commit esperado `C` usando **só** o
snapshot imutável, autenticando cada objeto no consumo e derivando a raiz dos bytes autenticados de
`C`; a representação selada é revalidada antes da entrega.

```yaml
301_S0_ARCHITECTURE_C:
  claim_scope: S_G_only
  boundary: "RunnerCanRead(Snapshot) AND NOT RunnerCanMutate(Snapshot)"   # not "uid == 0"
  chain: [AdmittedStorageCapability, PrivilegeSeparatedProducer, PhysicalSnapshot(staging),
          AtomicPublish(committed/<id>), RunnerReader(C11), AuthenticatedCommitBytes(C), DeriveRootTree(T),
          AuthenticatedClosure, Build, Seal, Revalidate]
  producer:
    input: admitted storage capability (C2_A)        # who authorized it: #331/C2_B, not proven here
    stage: physical_only                             # PhysicalSnapshot != AuthenticatedSubject
    forbidden_in_physical_stage: [git, zlib inflate, semantic validation, hash proof, pack parsing, verify-pack]
    copies: ["objects/ loose (raw, compressed)", "objects/pack/pack-*.{pack,idx}"]
    authors: [config (object format only), "HEAD -> refs/heads/none", "empty refs/heads, objects/info", receipt]
    never_copied: [source config, remotes, promisor, hooks, source HEAD, refs, packed-refs, alternates pointer]
    alternates: {authorized: flattened, unauthorized: refusal alternate_outside_authorized_storage}
    production_provenance: NOT_QUALIFIED             # U3
  publication:
    staging: "staging/<id>, producer-only (0700)"
    finalize: "files 0444, dirs 0555, owned by the producer identity"
    fsync_as_implemented: "each copied object file; the staging dir itself; committed/ after the rename"
    not_fsynced: [HEAD, config, receipt, subdirectories]            # T-F6
    commit_point: "rename(staging/<id> -> committed/<id>)"
    crash_before_commit_point: staging garbage, never a committed snapshot   # StagingGarbage != CommittedSnapshot
    staging_gc: future obligation
    S0_publication: {atomic_visibility: supported, crash_before_rename_does_not_publish: reproduced (SIGKILL, C10),
                     full_power_loss_durability: NOT_QUALIFIED}   # successor: S1_PUBLISH_01
  receipt:   # traceability only: SnapshotReceipt != ObjectAuthenticityProof
    fields: [schema_version, object_format, physical_bytes, physical_entries, alternate_sources_count,
             storage_capability_binding, published_snapshot_identity]
    contains_root_tree: false
    contains_commit: false
  budgets:   # two domains; neither is inferred from the other
    snapshot_budget: {domain: physical store, units: [bytes, entries, alternate depth], charged: fstat before read}
    closure_budget: {domain: subject closure, units: [nodes, payload, metadata, paths, component length],
                     charged: strict transport header before read}
  input_identity:
    expected_subject: {object_format, commit_oid, component_policy}
    independent_root_tree_authority: none            # reasons: R4-2, B-4
  object_format:
    source: expected_subject
    cross_checks: [commit_oid length, producer-authored snapshot config]
    live_repository_consulted: false
    supported: [sha1, sha256]
  reader:
    runs_as: unprivileged runner                     # required; otherwise STOP_301_C_READER_PRIVILEGE_DEPENDENCY (C11)
    AuthorizedReaderExecutionContext:   # EXTERNAL applicability precondition, established by the host/launcher (U3/S1/E)
      principal:
        expected_runner_identity: host/launcher-authorized
        ruid_euid_suid_fsuid_consistent: required
        rgid_egid_sgid_fsgid_consistent: required
        supplementary_groups: recorded_and_authorized
      namespace:
        user_namespace_identity: host/launcher-authorized     # T-F2
        mount_namespace_identity: host/launcher-authorized    # T-F1
      privilege:
        CapEff: 0
        CapPrm: 0
        CapInh: 0
        CapAmb: 0
        privilege_gain_on_exec: forbidden                     # X1; S1_CTX_01 (not implemented in S0)
      snapshot:
        producer_published_identity: required
        canonical_publication_locator: required
        identity_bound_to_authorized_mount_context: required  # T-F1
    context_authority: "ReaderSelfReport != ReaderContextAuthority: the reader cannot prove by itself that its user or
                        mount namespace is the authorized one; that provenance belongs to the future launcher/host"
    reader_side_checks_at_34fc575:   # experimental defense in depth, NOT the authority of the context
      - "uid/gid real == effective == saved == filesystem == expected (read from /proc/self/status)"
      - "CapEff == CapPrm == CapInh == CapAmb == 0; CapBnd recorded"
      - "absolute canonical path, no symlink component, no component/node owned by or writable (effective ids) for R"
      - "T-F1 (read-only bind view of writable inodes) and T-F2 (other user namespace) pass these checks"
    lessons: ["NumericUID != PrincipalIdentity", "NumericUIDInNamespaceA != SamePrincipalInNamespaceB",
              "Path != KernelObjectIdentity", "ReadOnlyMountView != UnderlyingObjectNotWritableThroughAnotherView"]
    activation_gate: "AuthorizedReaderExecutionContext not established -> S_G must not become trusted/consumable"
    subreaper: verified before git starts; a descendant surviving teardown -> refusal on success AND failure paths
    local_git: {target: committed immutable snapshot only, deadline: per object, parsing: strict header,
                memory_envelope: RLIMIT_AS per process where supported, aggregate_memory: NOT_CLAIMED}
    process_lifetime: "reader is a child subreaper; teardown kills and reaps every descendant (setsid included)"
    related_owner: "#320"
  applicability: |
    Applicable_SG(A,P,S,R,C) := AuthorizedStorage(A) AND PrivilegeSeparatedProducer(P,R)
                                AND ProducerPublishedSnapshot(P,S) AND AuthorizedReaderExecutionContext(R)
                                AND NOT PrincipalCanMutate(R,S) AND ExpectedCommit(C)
    Applicable_SG(...) AND SuccessfulCapture(C,S)  =>  AuthenticatedStableSubject(S_G)
    nothing is claimed outside Applicable_SG
  S_G:
    authority: expected C + content-addressed object map
    root_tree: derived from the authenticated bytes of C
    walk: one walk fetches and derives
    build_rechecks: every object in the map re-hashed to its oid and kind before use
    post_seal: seals read back, sealed bytes re-hashed, S_G re-derived from the map and compared with the sealed content
```

**B-findings — disposição** (sob a ratificação, válida dentro de `Applicable_SG`):

```yaml
B_findings_disposition:
  B1: ELIMINATED_BY_PRIVILEGE_BOUNDARY        # alternates injected after publish -> kernel EACCES (C3); source alternates only via capability (C9)
  B2: ELIMINATED_BY_PRIVILEGE_BOUNDARY        # promisor config injected after publish -> EACCES (C4); no fetch (C4/C8)
                                              # review of a858dc9 (RC-1): reproduced again via a NON-canonical path handed to
                                              # the reader; refused by the reader checks, but TF1/TF2 show those checks are
                                              # context-relative: elimination holds inside AuthorizedReaderExecutionContext
  B3: ELIMINATED_FROM_PHYSICAL_PRODUCER       # producer never inflates (C5); expansion only inside the contained reader unit
  B4: AUTHORITY_REDUCED_TO_AUTHENTICATED_COMMIT   # C + content-addressed map; aux root has no effect, ablation changes S_G (C6)
  B5: DISCRIMINATED_BY_DESCENDANT_SURVIVOR_CONTROL   # setsid grandchild: unit 0 survivors, process-group ablation leaves one (C7)
  B6: PARTIAL                                 # tmp_obj_* skipped by name (C2); select with fd >= 1024 and corrupted-pack ablation not retested
```

**Disposição do mantenedor para RC-1** (revisão de `a858dc9`): `finding_established: true`,
`architecture_C_boundary_refuted: false`, `LOCAL_ENFORCEMENT_DEFECT_OF_READER_APPLICABILITY`. O
contramodelo só era admitido porque o leitor aceitava um locator que não correspondia ao snapshot
canônico publicado (symlink/ancestral gravável pelo runner). Isso **não** vale como "corrigido porque
o caminho parece canônico": a conclusão depende do `ReaderPrincipal` formalizado acima (K1). Um
contramodelo em que o snapshot canônico publicado pelo produtor continue alterável pelo leitor sob o
principal do runner ratificado é `STOP_301_C_BOUNDARY_RECURRENCE`.

**Ratificação do mantenedor (revisão de `34fc575`).**

```yaml
architecture: {A: REJECTED, B: REJECTED, C: RATIFIED_AS_COMPONENT_CONTRACT}
TF1: {established: true, architecture_C_boundary_recurrence: false, disposition: OUTSIDE_RATIFIED_READER_EXECUTION_CONTEXT}
TF2: {established: true, architecture_C_boundary_recurrence: false, disposition: OUTSIDE_RATIFIED_READER_EXECUTION_CONTEXT}
```

Isso **não** quer dizer que TF1/TF2 sejam inválidos ou corrigidos: eles continuam falsificadores
obrigatórios da ativação de S1 (§12). A razão é estreita. Nenhum principal aceito, no contexto do
snapshot canônico publicado pelo produtor, alterou algo que o Git local consome. E nenhuma
recorrência autenticar-X/consumir-Y foi estabelecida na identidade de S_G.

**Relações com owners** (nenhuma absorvida):
- **#331 / C2_B:** C prova só "dada a capability A, o produtor nunca sai de A" (C9). Quem autorizou
  A continua com #331.
- **#319:** proveniência de `C`. C só autentica o fechamento **de** `C`.
- **#320 (adendo):** "Git local sem prazo pode travar" é **candidato a mecanismo** registrado sob
  #301 (Spike B, EXP-SNAP, C7); a família de disponibilidade/recurso do Git local continua com
  #320, que **não** é fechada. P1b (NFS/FUSE) é outro mecanismo e não é tratado aqui.
- **#354:** resíduo de ciclo de vida de descritor; S não o ativa (§8).
- **U3:** proveniência do produtor em produção continua aberta: o produtor do experimento é o root
  de um container efêmero (`SpikeContainerRoot != ProductionHostAuthority`).


#### Predecessor B — decisão de fronteira registrada em `8c4842b` (REJECTED; preservada como histórico)

```yaml
architecture_as_recorded_at_8c4842b:
  A: {name: LIVE_GIT_TRANSPORT, rounds: [R1 corrected, R2 STOP (S_D narrowed), R3 STOP, R4 terminal recurrence],
      status: REJECTED_AFTER_R4}        # PR #355 comments 5852251027, 5852389024, 5852664959
  B: {name: PRIVATE_REMOTELESS_SNAPSHOT, evidence: Spike B (comment 5852938161) + this head,
      status: CANDIDATE_AT_8c4842b}      # later REJECTED: PR #355 comment 5853311835 (B-1..B-4)
301_S0_ACQUISITION_BOUNDARY_DECISION:
  live_git_against_subject_repo: {permitted_for_S_G_capture: false}
  physical_acquisition: {mechanism: descriptor_anchored_copy, git_invocations: none, network_completion: forbidden,
                         missing_required_object: refusal, partial_clone_policy: offline_closure_completeness}
  private_snapshot: {trusted_as_content_at_read_time: false, same_uid_mutation_resistant: false,
                     purpose: isolated acquisition source}
  closure_reader: {source: private_snapshot_only, git_use: local_remote_less_only,
                   resource_controls: [timeout, memory, process ownership], related_owner: "#320"}
  S_G: {hash_on_read: required, same_bytes_authenticated_and_committed: required,
        acquisition_identity: [object_format, commit_oid, root_tree_oid, component_limit], seal: required,
        post_seal_revalidation: required}
  boundaries: {anchor_provenance: "#319", host_storage_provenance: "#331", producer_bootstrap: U3,
               dependencies: future_S_D, execution: future_E}
  rejected: {option_1_as_primary_boundary: true, option_3_custom_git_pack_reader: true}
spike_B_adjudication_qualifications:
  - R4-1: original mechanism (live-repo lazy fetch / process tree) ELIMINATED BY CONSTRUCTION; the broader family
          (local git CPU/memory/time over the snapshot) stays OPEN, owner #320 — CountermodelClosed != FailureFamilyExhausted
  - R4-2: countermodel established; mechanism now INTEGRATED in this candidate and discriminated; closed only after
          exact-head review
  - verify-pack: not required for S_G authenticity; a separate availability/early-corruption role is not denied
  - host 3.12/git 2.43 evidence was viability; the recorded run here is the declared runtime (3.11/git 2.39)
  - equal S_G digest across boundaries is a positive control on THIS corpus, not universal equivalence
```

O bloco acima é **histórico**: registra a decisão que B tomou, e o motivo da rejeição está no
comentário 5853311835 (o snapshot de B continuava gravável pelo mesmo UID; `config`/`alternates`
injetados mudavam o **comportamento** do Git). As qualificações de linguagem sobre R4-1/R4-2 e
`verify-pack` continuam válidas. Sob C, a decisão (a) vale **só** para o Git local sobre o snapshot
**imutável ao runner**; o Git nunca lê o repositório vivo na captura de S_G. (b), (c) e (d)
continuam.

S0 fecha o contrato de **S_G** como componente, ratificado sob a arquitetura C e aplicável só dentro de `Applicable_SG` (§2). S_D e E continuam **propostos**: a evidência
deles aqui é de viabilidade, não de qualificação. Nenhuma obrigação foi apagada; as de S_D têm
owner futuro e contramodelos obrigatórios (§7.3).

Este documento é a **única** fonte legível da proposta. `EVIDENCE.md` aponta para cá e não
repete norma. Vocabulário epistemológico e critérios de STOP/REDESIGN pertencem a
[`STRUCTURAL_CHANGE_PREFLIGHT.md`](../STRUCTURAL_CHANGE_PREFLIGHT.md); nada aqui os substitui.
ADRs CAEM 0011/0012/0016 (lidas em `mglpsw/caem@854e321a`) são **referência de desenho**, sem
autoridade (`config/caem/caem-3.0-f0.pin.json`: `authority_effect: none`).

## 1. Pergunta, decisões preservadas e distinções

> Qual representação S, sob quais autoridades e premissas de ambiente, permite que o futuro
> consumidor E execute o AgentReview real sem retornar a bytes mutáveis não autenticados?

`G` = subject Git admitido; `M` = materialização mutável de C3; `Q` = igualdade observacional
`G == M` sob quiescência, no domínio ratificado em #333 (Linux + C3 admitido + `PC_NAME_MAX`
positivo pelo descritor). Q **não** é transferido a S: Q não exclui escritores e não prova os bytes
carregados. O objetivo de #301 (obter S e E) está decidido; esta slice propõe a **forma** de S, o
envelope de execução e a base de confiança.

Três subjects, que o desenho mantém separados:

| Subject | O que é hoje no caller real | Papel em S/E |
|---|---|---|
| **Engine** (toolrepo) | checkout de `aiops-orchestrator` num SHA completo, importado via `PYTHONPATH` | **código executado**: entra em S |
| **Target / PR** | diff reacquirido pela API; profile/policy/artefatos do checkout *base* do target | **dado**: nunca é código autorizado a executar; não entra em S |
| **Dependências + runtime** | venv criada por `install-agent-review-toolrepo.sh` (`pip --require-hashes`) sobre o `python3` do host | wheels do lock → S_D; interpretador/stdlib → TCB |

Há um quarto papel que o caller real mistura com a engine: o **adapter do target**
(AgentEscala `scripts/aiops/agent_review_v2_run.py`, base-owned) é `__main__` do mesmo processo,
importa a engine e chama o Router entre `build` e `bind`. AgentEscala, CAEM e SACR-AS são
consumidores heterogêneos; nenhum vira ramo especial da engine. Ver §9 (U1).

## 2. Claim de S0 (≤ 3 frases), propostas restantes e non-claims

> **S_G-CLAIM (arquitetura C, ratificada como contrato de componente).** Dados uma capability de
> storage admitida (#331), um produtor com separação de privilégio em relação ao runner, um snapshot
> canônico publicado por esse produtor — só bytes físicos, sem Git, sem inflate e sem metadata da
> fonte — e um `AuthorizedReaderExecutionContext` autorizado pelo host/launcher (principal, user
> namespace, mount namespace e política de exec), no qual o snapshot **não** é alterável pelo
> principal do leitor, e limites de admissão explícitos, S_G é derivado de um commit esperado `C`
> (#319) usando só esse snapshot: Git local contido (prazo,
> memória por processo, subreaper com teardown da unidade inteira), cada objeto cobrado a partir de
> um cabeçalho estrito antes de ser lido e autenticado **no instante da leitura** contra `C` pela
> cadeia commit → tree → blob no formato de objeto de `C`, com a raiz derivada dos bytes
> autenticados de `C`, codificando sem perda as distinções da contract A/A2 de C3 sob esses limites.
> A capability só é emitida depois do instante de compromisso — selos
> `F_SEAL_{WRITE,GROW,SHRINK,SEAL}` lidos de volta, conteúdo selado re-hasheado e S_G **re-derivado**
> do mapa de objetos endereçado por conteúdo e comparado com o conteúdo selado — e a partir daí
> nenhum processo sem `CAP_SYS_ADMIN`/root, inclusive do mesmo UID, altera bytes, índice ou tamanho
> de S_G; o consumidor valida selos, digest e identidade `(algoritmo, C)` recebidos por canal
> separado sobre o mesmo buffer que consome.

```text
Applicable_SG(A,P,S,R,C) := AuthorizedStorage(A) ∧ PrivilegeSeparatedProducer(P,R)
                            ∧ ProducerPublishedSnapshot(P,S) ∧ AuthorizedReaderExecutionContext(R)
                            ∧ ¬PrincipalCanMutate(R,S) ∧ ExpectedCommit(C)
Applicable_SG(...) ∧ SuccessfulCapture(C,S)  ⇒  AuthenticatedStableSubject(S_G)
```

Nada é afirmado fora de `Applicable_SG`. `AuthorizedReaderExecutionContext` é uma **precondição
externa de aplicabilidade**, estabelecida pelo host/launcher (U3/S1/E), e **não** uma propriedade
que S0 impõe em runtime: `ReaderSelfReport != ReaderContextAuthority`. As checagens do leitor no
head experimental `34fc575` (principal lido de `/proc/self/status`, capabilities, caminho canônico,
escrita com credenciais efetivas) são defesa em profundidade e são **contornáveis fora do contexto
autorizado**: T-F1 (vista read-only por bind mount de inodes graváveis por outro caminho) e T-F2
(ids numéricos do runner vistos dentro de outro user namespace) passam por elas, e o Git executa o
helper do runner. Por isso a unidade de aplicabilidade não é `uid == 2000` nem
`access(snapshot, W_OK) == false`, e sim o contexto autorizado.

**Portão de ativação (objetivo de produto de #301).** Uma ativação de produção (S1/E) **não pode**
rodar nos contextos de T-F1 ou T-F2: `AuthorizedReaderExecutionContext` não estabelecido ⇒ S_G não
pode se tornar confiável nem consumível. T-F1 e T-F2 são **falsificadores obrigatórios** da ativação
de S1 (§12), não casos excluídos da prova.

**Propostas que S0 não qualifica** (arquitetura preservada, prova futura):
- **S_D**: dependências como segundo container selado vinculado ao lock em S_G (§4–§5, §7.3).
- **E**: launcher/bootstrap/loader/canal (§9, §7.2). A execução da engine real só a partir de S_G +
  S_D, com resultado idêntico ao caminho normal, é evidência de **viabilidade**, não claim de S0.

Premissas (não impostas por S): kernel Linux com memfd seals (observado só em 6.18/WSL2); root e
kernel confiáveis; `C` correto (#319); a capability de storage admitida (#331); um produtor com
separação de privilégio cuja **proveniência de produção** não é qualificada aqui (U3; o experimento
usa o root de um container efêmero: `SpikeContainerRoot != ProductionHostAuthority`);
launcher/leitor/bootstrap vindos de local não gravável pelo UID do runner (§6, U3); a mesma premissa de integridade de processo de §3 vale para **produtor e
launcher** (o heap do produtor guarda bytes autenticados antes do selo; o launcher guarda os digests
esperados); o kernel impõe `RLIMIT_AS` ao filho `git`. **SHA-1:** para repositórios sha1, a
autenticação verifica igualdade com um oid fixo e ancorado externamente e assume **resistência a
segunda pré-imagem** do SHA-1 para esse oid; a verificação usa `hashlib.sha1` e **não** alega
equivalência com a detecção de colisão (sha1dc) do git. Endurecimento futuro possível: sha1dc ou
admissão só sha256 — nova obrigação, não importada para S1.

Non-claims: que o leitor, sozinho, estabeleça ou prove o `AuthorizedReaderExecutionContext`
(principal, user namespace, mount namespace, não-escalada no `exec`); que uid/gid numéricos
autentiquem um principal; durabilidade a queda de energia da publicação; quem autorizou a capability (#331/C2_B); proveniência do commit (#319); proveniência do
produtor em produção (U3); qualificação de CT104; memória **agregada** da unidade Git (`RLIMIT_AS` é
por processo); ausência universal de DoS; limpeza automática de staging após crash; prazo total da
captura; S_D, E, proveniência de execução, G5 e prontidão de release; integridade do **processo**
além da premissa de §3; disponibilidade (um ator same-UID pode matar/esgotar recursos); integridade dos **dados do target** (§9 U4); autenticação do
interpretador/stdlib (piso por ownership, §6); proveniência do anchor (#319); correção do
comportamento da engine; portabilidade não-Linux; qualificação de CT104; modo Router conectado;
relançamento de brokers/supervisor; confidencialidade de S (não é segredo).

## 3. Atores e capacidades (domínio de ameaça proposto)

"Resiste a same-UID" é decomposto por operação. **O** = integridade do objeto selado; **P** =
integridade do processo consumidor; **R** = canal de resultado; **A** = disponibilidade.

| Ator | Capacidades relevantes | Coberto por S/E | Não coberto |
|---|---|---|---|
| Conteúdo Git/PR hostil (target) | dados arbitrários no diff/artefatos | nunca executado; parse pelas autoridades de ingress existentes | semântica dos dados (fora de S) |
| Conteúdo do commit da engine | árvore arbitrária sob `C` admitido | recusas estruturais (C3) e orçamentos por ocorrência (EXP-RES) | `C` errado → #319 |
| Writer de filesystem mesmo UID (não-ancestral) | reescrever/renomear checkout, `.git/objects`, venv, `__pycache__`, `pyvenv.cfg`, `.pth`, wheels, dados do target; abrir `/proc/<pid>/fd/N` de processos *dumpable*; escrever em pipe reaberto; tocar memfd **antes** do selo; adicionar selo estranho; manter mapeamento | **O**: pós-compromisso nenhuma escrita/grow/shrink/punch/mmap-write/mprotect (EXP-CAPTURE); pré-compromisso detectado ou recusado; adulteração de objeto Git recusada por hash (EXP-N1). **R**: socketpair não reabrível (EXP-PROC). Bytes carregados independem de M/venv/pyc/plugins (EXP-FUNC) | **A** (kill, OOM, disco); leitura de S (não secreto); dados do target lidos por path |
| Worker legítimo concorrente | mesmo que acima, benigno | S não compartilha estado mutável | — |
| Ator que altera configuração do processo (não o arquivo) | env (`LD_PRELOAD`, `PYTHON*`, `HOME`), cwd, `pyvenv.cfg`, user site, `.pth`, `sitecustomize` | launcher: interpretador root-owned por caminho absoluto, `env={}`, `-I -S`, `cwd=/` (EXP-BOOT) | se o ator **for** o launcher ou um ancestral |
| Interferência no processo (ptrace, `/proc/pid/mem`) | com Yama `ptrace_scope ≥ 1`, não-ancestral não faz attach nem escreve memória; lista FDs enquanto dumpable | **P** somente sob a premissa Yama≥1 ∧ ator não-ancestral, para consumidor, produtor e launcher (EXP-PROC observou o consumidor) | ancestrais (runner/agent) são confiados; `ptrace_scope=0` quebra **P** |
| Sinais (`kill`, `SIGINT`, `SIGSTOP`) de mesmo UID | interromper ou matar produtor, launcher ou filho | nenhum resultado parcial vira S/sucesso (301S-LIFE) | **A**; não testado com sinais reais |
| Runner (UID do runner) sobre o snapshot publicado (arquitetura C) | tentar escrever `config`, criar `objects/info/alternates`, reescrever/renomear objetos e packs, `unlink`, `mkdir`, `chmod`, renomear o snapshot, criar irmão em `committed/`, listar `staging/` | **negado pelo kernel** (EXP-ARCH-C C1/C3/C4/C10: EACCES/EPERM); o leitor recusa um snapshot que ele próprio poderia alterar (C11) | ator com outro privilégio sobre o storage (fora; produtor/root confiados); proveniência do produtor em produção (U3) |
| `git` local sobre o snapshot | o repositório vivo (config, remotos, promisor, helpers) **não** é lido pelo Git na captura; config/HEAD/refs do snapshot são do produtor e imutáveis ao runner | mecanismo R4-1 **eliminado por construção**; B-1/B-2 (metadata mutável que muda o comportamento do Git) eliminados pela fronteira de privilégio (C2/C3/C4, C8: marcador de busca ausente); leitor com prazo, `RLIMIT_AS`, cabeçalho estrito e teardown da unidade inteira via subreaper (C7) | família de recursos do Git local (CPU/memória agregada/tempo total) **aberta**, owner #320 |
| Bootstrap, consumidor, produtor, leitor, futuro produtor de receipt | papéis TCB; o produtor do snapshot tem privilégio **separado** do runner, e o leitor roda **como** o runner | ver §6 | auto-autenticação recursiva; proveniência do produtor em produção (U3) |
| root / kernel | tudo | confiados (declarado) | — |
| UID diferente | DAC | fora | — |

Redução proposta em relação ao texto histórico de #301 ("exclusively satisfied from the verified
subject"): **P** vale só sob a premissa Yama/não-ancestral e **A** não é garantida. Esta redução é
**proposta para adjudicação**, não ratificada aqui.

## 4. Representação S proposta

**Forma.** Dois containers com o mesmo formato canônico (protótipo:
[`experiments/s0_bootstrap.py`](experiments/s0_bootstrap.py) `serialize`/`parse`), cada um num
único memfd selado:

- `S_G` — **árvore completa** de `C` (não um subconjunto escolhido): cabeçalho
  `(algoritmo, commit, root_tree)`, depois nós em ordem canônica de bytes brutos do path
  (`memcmp`), cada nó `(kind ∈ {tree, regular, executable, symlink}, path bruto, oid, len, payload)`.
  Diretórios explícitos e vazios são nós; symlink é `kind=symlink` com o target como bytes.
- `S_D` — conjunto de dependências: cabeçalho `(wheelset-sha256, sha256(lock), sha256(manifest de
  wheels))`, nós = membros de wheels (inclusive `.so`) com o sha256 de cada membro.

Índice, offsets e tamanhos estão **dentro** dos bytes selados e cobertos pelo mesmo digest; não há
índice externo substituível. O parser recusa truncamento, bytes finais, índice fora de ordem ou
duplicado.

**Identidades distintas** (nunca fundidas): `container_digest` = sha256 dos bytes de `S_G` (ou
`S_D`); `subject_identity` = `(algoritmo, C)`; `dependency_identity` = `sha256(lock)` + digests
dos wheels; `invocation` = interpretador/flags/env/argv do launcher; `observation` = o que o
bootstrap registrou. Hoje o repositório tem campos de identidade de execução (G1); **nenhum campo
de wire/schema novo é proposto aqui**.

**Instante de compromisso.** Antes dele a captura pode falhar livremente; nenhum descritor sai do
produtor. Compromisso = `F_ADD_SEALS(WRITE|GROW|SHRINK|SEAL)` ok ∧ `F_GET_SEALS ⊇` esse conjunto ∧
`sha256(pread(conteúdo selado)) == sha256(bytes serializados)` ∧ **revalidação do objeto selado**:
parse do conteúdo selado; identidade `(algoritmo implicado por C, C, root_tree)`; conjunto de nós,
tipos e oids igual ao **registro da aquisição** (o que a caminhada autenticada admitiu); cada folha
re-hasheia ao seu oid no formato de objeto Git; todo pai é nó `tree`. Falha em qualquer passo fecha
o fd uma vez e não retorna nada. O re-hash pós-selo pega o escritor da janela pré-selo; só a
revalidação pega "autenticar A e entregar B" antes da serialização, porque aí o digest do selo é
calculado sobre B (EXP-CAPTURE: ablação sem revalidação **compromete B**). O objeto final — não um
spool intermediário — é o truth-maker. Os corpos de tree não são embutidos: seus oids ficam
vinculados pelo registro da aquisição, não re-hasheados após o selo.

**Não** são substitutos de estabilidade: permissões `0700`/`0444`, mutex Python, path resolvido,
FD mantido aberto, ou repetir hashes sobre M (spike EXP-Q1; K03/K05 de #324).

## 5. Cadeia de autenticação

```text
capability de storage admitida (#331)
 └─ produtor (privilégio separado do runner): cópia física por descritor — sem Git, sem inflate, sem metadata
    da fonte; alternates autorizados achatados; orçamento físico; staging → finalize → rename = commit point
     └─ committed/<id>: RunnerCanRead ∧ ¬RunnerCanMutate (kernel)
C (esperado, via #319; expected_subject = {object_format, commit_oid, component_policy})
 └─ leitor COMO o runner: recusa snapshot mutável por ele; Git local só sobre committed/<id>
    (prazo por objeto, RLIMIT_AS por processo, cabeçalho estrito, subreaper + teardown da unidade)
 └─ commit object  ── hash(type ‖ ' ' ‖ len ‖ NUL ‖ body) == C ; 1ª linha "tree <oid>"
     └─ tree objects ── mesmo hash; bytes interpretados por C3 `_parse_tree_data` (dono da regra)
         └─ blobs      ── mesmo hash; ESTE buffer é o payload embutido em S_G
             └─ blob requirements-agent-review.lock  (dentro de S_G)
                 └─ sha256 por distribuição/versão
                     └─ wheel lido UMA vez; sha256 ∈ lock   (artifact adquirido)
                         └─ membros extraídos DESTE buffer; cada um == RECORD do wheel
                             └─ S_D ; header vincula sha256(lock)
```

- **Snapshot físico com separação de privilégio (arquitetura C).** O object store sai do domínio
  vivo **antes** de qualquer pergunta sobre `C`, e sai para um lugar que o runner **não** escreve:
  o produtor abre o storage por descritor dentro da capability admitida (primitivas da G1C,
  reutilizadas **sem** o inflate de loose nem `verify-pack`), copia objetos loose na forma
  comprimida original e `pack-*.pack/.idx`, achata só alternates autorizados (fora da capability →
  `alternate_outside_authorized_storage`) e cobra um **orçamento físico** (bytes, entradas,
  profundidade de alternates) pelo `fstat`, antes de ler cada arquivo. Nomes que não são objeto do
  formato (ex.: `tmp_obj_*` do Git vivo) são ignorados e contados. Nada de `config`, remotos,
  promisor, hooks, `HEAD`, refs, `packed-refs` ou ponteiro de alternates da fonte atravessa: o
  produtor escreve `config` (só o formato de objeto), `HEAD → refs/heads/none`, `refs/heads` e
  `objects/info` vazios e o recibo. Finalização: arquivos `0444`, diretórios `0555`; `fsync` só de
  cada objeto copiado, do diretório de staging e de `committed/` depois do rename — HEAD, `config`,
  recibo e subdiretórios **não** recebem `fsync` (T-F6). A publicação tem **visibilidade atômica**;
  um crash (SIGKILL) antes do rename não publica (C10); durabilidade a queda de energia **não é
  qualificada** (S1_PUBLISH_01). O **commit point** é `rename(staging/<id> → committed/<id>)`. Antes dele o runner nem lista o
  staging; um crash deixa lixo de staging, nunca snapshot publicado (C10). `PhysicalSnapshot !=
  AuthenticatedSubject`: nenhuma validação de conteúdo acontece aqui, e o recibo
  (`SnapshotReceipt != ObjectAuthenticityProof`) não tem commit nem root_tree. Ele conta só o que de
  fato conta: `physical_bytes`/`physical_entries` são os arquivos-fonte **cobrados** (inclusive um
  objeto deduplicado entre fonte e alternate, que é cobrado mas não copiado de novo: T-F6), e
  `physical_content_sha256` cobre os objetos copiados — não HEAD, `config`, o recibo nem
  diretórios. A propriedade
  normativa é `RunnerCanRead ∧ ¬RunnerCanMutate`, estabelecida pelo kernel; `uid == 0` é só o
  mecanismo do experimento. Um objeto obrigatório ausente é recusa tipada (`object_missing`), sem
  busca: a política é `OfflineClosureComplete(C)` (C4/C8B), e um clone parcial com closure completa
  é admitido (C8A).
- **Leitor como o runner (C11), checagens do leitor (K1) e o contexto autorizado.** A derivação de
  S_G roda sem privilégio. A aplicabilidade depende do `AuthorizedReaderExecutionContext`, que vem
  do host/launcher. O leitor **não** o estabelece nem o prova: suas checagens são defesa em
  profundidade e não substituem esse contexto (T-F1/T-F2 as contornam fora dele). No head
  experimental `34fc575`, as checagens são estas. Primeiro o **principal**: uid real,
  efetivo, salvo e de filesystem, lidos de `/proc/self/status`, têm de ser iguais ao uid do runner
  **esperado** (dado pelo chamador), e o mesmo vale para os gids (`reader_principal_mismatch`);
  `CapEff`, `CapPrm`, `CapInh` e `CapAmb` têm de ser zero (`reader_has_capabilities`); grupos
  suplementares e `CapBnd` são registrados. `ReaderPrincipalIdentity != ReaderSelfAssertion`, e
  ids numéricos lidos no próprio processo continuam relativos ao user namespace (T-F2):
  `os.getuid()` não basta, porque o Git e as syscalls usam as credenciais efetivas/de filesystem, e um
  uid salvo ou uma capability permitida podem ser reativados depois da checagem (K1A/K1B/K1C: o
  contramodelo de cada um **altera** o snapshot, e o detector de `28a3b4a` o aceitava). O caminho recebido tem de ser absoluto,
  canônico e **sem symlink em nenhum componente** (`snapshot_path_not_canonical`), para que o
  caminho que o Git re-resolve seja o caminho verificado. Nenhum componente, de `/` até o snapshot,
  e nenhum nó abaixo dele pode ser do uid efetivo ou gravável pelas credenciais **efetivas**
  (`faccessat` com `AT_EACCESS`, que inclui gid efetivo e grupos suplementares)
  (`snapshot_mutable_by_reader`). A revisão de
  `a858dc9` mostrou que a checagem **lexical** anterior aceitava um caminho com symlink root-owned
  apontando para um diretório do runner, e o runner então trocava o snapshot e fazia o Git executar
  o seu helper (mecanismo de B-2); ver EVIDENCE. A checagem de escrita também é relativa ao mount:
  uma vista read-only de inodes graváveis por outro mount passa (T-F1). Se a
  derivação precisasse do privilégio do produtor, o resultado seria
  `STOP_301_C_READER_PRIVILEGE_DEPENDENCY`. Quem cria esse principal (o launcher) **não** é provado
  por S_G: a composição pertence a U3 / à futura composição de execução, e
  `ProducerPrivilegeSeparation != ProductionProducerProvenance`.
- **Algoritmo** vem **só** do `expected_subject.object_format` (sha1 ou sha256) e é conferido com o
  comprimento de `C` (`expected_subject_invalid`) e com o `config` escrito pelo produtor
  (`snapshot_format_mismatch`); o repositório vivo nunca é consultado. Nada de SHA fixo por
  conveniência; a regra JSON de self-hash do produto não se aplica a objetos Git.
- **Identidade de entrada = `expected_subject {object_format, commit_oid, component_policy}`.** Não
  existe autoridade independente de root_tree (R4-2, B-4): a raiz é **derivada** da primeira linha
  do corpo autenticado de `C`. Os objetos lidos ficam num mapa endereçado por conteúdo; uma única
  caminhada busca e deriva; na construção, cada entrada do mapa é re-hasheada ao seu oid e tipo
  (`object_map_binding_mismatch`); depois do selo, S_G é re-derivado do mapa e comparado ao conteúdo
  selado (`sealed_derivation_mismatch`). Um registro auxiliar apontando outra tree não tem efeito; a
  ablação que confia nele muda S_G (C6).
- `git cat-file --batch` **sobre o snapshot imutável** é **transporte não confiável e contido**
  (decisão a, restrita ao snapshot): sessão própria, **prazo por objeto** (um objeto malformado trava
  o `cat-file`; sem prazo a captura não termina — EXP-SNAP, com ablação) e **tempo de vida da
  unidade inteira**: o leitor é *child subreaper*, então todo descendente, inclusive um que faça
  `setsid`, volta a ele, e o teardown mata e colhe a subárvore até não restar nenhum (C7: 0
  sobreviventes; a ablação que só mata o grupo de processos deixa o neto `setsid` vivo). A limpeza
  do experimento usa pid + starttime + nonce, nunca só o PID. O spike e EXP-N1
  mostram que ele serve, com rc 0, bytes que não hasheiam ao oid pedido. A autenticação é do buffer
  lido, logo independe do storage (loose, pack, alternates) — argumento, observado apenas para
  loose. O filho roda sob `RLIMIT_AS` aplicado antes do `exec`; o cabeçalho é lido com limite e
  parseado estritamente (oid exato, tipo conhecido, decimal não negativo de até 19 dígitos) antes de
  qualquer cobrança; toda recusa mata e colhe o filho antes de propagar. Fechar o pipe e esperar
  deixava o filho expandir o objeto; matar rápido sem limite é corrida, não bound (EXP-RES `git_child_contained_*`).
- **Bytes autenticados = bytes incorporados.** Hash de uma leitura e cópia de outra reabre a janela
  (EXP-N1 `MUTANT_verify_then_reread`). A única transformação é a extração de membros do wheel, que
  preserva a equivalência pelo RECORD autenticado junto com o wheel; nomes duplicados, `..`,
  absolutos, symlinks, `.data/`, `.pth` e membros fora do RECORD são recusados.
- A venv **não** é fonte de bytes: `pip --require-hashes` autentica o download que o pip fez, não os
  arquivos que um processo lê depois (EXP-FUNC: venv adulterada e plugin plantado executam no
  caminho normal).
- Uma cópia selada com digest próprio **não** autentica a origem: o digest de `S_G` só significa
  algo porque cada nó foi verificado contra `C`, e `C` vem de #319.
As regras abaixo para S_D são **propostas** (§7.3): o protótipo as observa no corpus sintético, mas
a ingestão de S_D não é limitada em `1e2453e` (R2-1/R2-2) e sua closure nativa está incompleta (R2-3).

- **O lock precisa ser nó `regular` de `S_G`**; o texto-alvo de um symlink nunca autoriza S_D
  (produtor e consumidor verificam).
- **Identidade do wheel vem de dentro dos bytes autenticados**, não do nome do arquivo: stem do
  `.dist-info`, `Name`/`Version` do `METADATA` e tags do `WHEEL` (expandidas como no nome, pois
  geradores como maturin gravam tags comprimidas) têm de coincidir com a entrada do lock; as tags
  têm de ser compatíveis com o interpretador alvo, cuja tag (`cp311`) fica no cabeçalho selado de S_D
  e é reconferida pelo consumidor.
- O lock é lido de forma **mais estrita que o pip**: nome duplicado, marker/extra e hash fora de
  token `--hash=` (inclusive em comentário) são recusados, nunca mesclados.
- Tamanho de cada membro é checado pelo `ZipInfo` **antes** de descomprimir; colisões
  arquivo/diretório (no mesmo wheel ou entre wheels) são recusadas.
- Nativos: a seção dinâmica ELF é inspecionada; `RPATH`/`RUNPATH` ou `DT_NEEDED` com `/` são
  recusados, de modo que a closure nativa fique no caminho do ld.so root-owned (TCB).

## 6. Closure, dependências e TCB

### 6.1 Censo do percurso escolhido

**Recorte.** O percurso material é o que o caller real v2 executa (AgentEscala
`agent-review-v2-analysis.yml` → `agent_review_v2_run.py` @ `develop 8537eb18`): profile YAML →
policy de agrupamento → `parse_unified_diff` → `assemble_manifest_from_diff_v2` →
`build_chunk_payloads_from_profile_v2` → `emit_payload_set_v2` → `bind_chunk_response_v2` →
`parse_bound_chunk_response_v2` → `synthesize_chunk_results_v2` → `compute_readiness_decision_v2` →
redação do bundle. A chamada ao Router é substituída por resposta sintética válida (a engine a
consome como dado); reaquisição da PR e teto de rollout são do adapter. Resultado:
`readiness=ready`, 2 chunks, `bundle_sha256` idêntico ao caminho normal (EXP-FUNC). Não é só
`import app.agent_review`.

| Componente | Origem | Consumidor | Carga | Efeito necessário | Bytes / metadata | Autoridade existente | Candidato |
|---|---|---|---|---|---|---|---|
| `app`, `app.agent_review` + 31 submódulos, `app.common`, `app.common.strict_json` | commit `C` do toolrepo | driver/adapter | inicial, imports da engine | toda a lógica | 35 módulos-fonte | #319 (anchor) + hash-on-read | **S_G** (raiz importável `app`) |
| demais arquivos de `C` (scripts, tests, docs, lock) | `C` | produtor (lock); ninguém importa | — | fidelidade de G; lock | árvore completa | idem | **S_G** como dado; raízes importáveis restritas a `app` |
| `pydantic` (42 módulos), `annotated_types`, `typing_extensions`, `typing_inspection` | wheels do lock | engine | inicial + lazy (`pydantic._migration`, `plugin._loader`) | modelos/validação | fontes `.py` | lock ∈ `C` + RECORD | **S_D** |
| `pydantic_core._pydantic_core` (nativo) | wheel cp311 manylinux | pydantic | inicial | validação | `.so`; NEEDED: libgcc_s, librt, libpthread, libm, libc, ld-linux; sem RPATH/RUNPATH (verificado pelo produtor) | lock + RECORD + identidade/tags | **S_D**; carregado de memfd selado **no filho** via `/proc/self/fd/N` |
| `yaml` + `yaml._yaml` (Cython) | wheel PyYAML | profile loader | inicial (`yaml.cyaml`) | engine usa só `SafeLoader` (Python); `_yaml` é inicializado mas não usado | `.so`; cria `_cython_3_0_11`, `cython_runtime` | lock + RECORD | **S_D** (incluído por paridade; exclusão exigiria decisão explícita) |
| metadata de distribuição (`*.dist-info`) | wheels | `importlib.metadata` (pydantic plugin loader) | tardio, 1ª criação de validator | nenhum no percurso | presentes em S_D como dados | — | **recusado como canal**: E não expõe distribuições; plugins pydantic **não suportados** |
| interpretador CPython 3.11 (+ `libpython3.11.so.1.0`) | host (root-owned) | launcher | antes de tudo | — | `/usr/local/bin/python3.11` no experimento | ownership/DAC | **TCB** |
| stdlib (103 fonte, 27 ext, 41 builtin/frozen) | host (root-owned) | engine, bootstrap | inicial | — | `sys.base_prefix/lib/python3.11` | ownership/DAC | **TCB** |
| ld.so, glibc, libgcc_s | host (root-owned) | carga nativa | antes do `main` e no dlopen | — | ld.so.cache | ownership + launcher fecha `LD_*` | **TCB** |
| `site`, `.pth` (inclusive `_distutils_hack` da venv), `sitecustomize`, user site, `pyvenv.cfg` | venv/usuário | startup | antes do 1º import | nenhum | mutáveis same-UID | — | **recusado** (`-I -S`, sem venv) |
| `__pycache__/*.pyc` do checkout | escrito pelo caminho normal | import normal | substitui a fonte | nenhum | mutável | — | **recusado**: E compila de S (EXP-FUNC mostra `.pyc` plantado com fonte idêntica ao commit executando no caminho normal) |
| dados do target (profile YAML, artefatos, contratos) | checkout base do target | `external_path_ingress_v2` (#200-G4B) | tardio, 2 aberturas por arquivo (valida → relê por path) | entrada do review | por path | ingress declara same-UID fora de escopo | **fora de S; aberto (U4)** |
| diff, respostas do Router | API / Router | engine | por valor | entrada | — | adapter | **fora de S** (dado) |
| adapter do target, Router client | checkout base do target | `__main__` do processo | inicial | orquestração, rede | — | target | **fora de S; aberto (U1)** |
| brokers/supervisor (`__file__` para relançar) | `C` | executor de checks | fora do percurso | relançar por path | `BROKER_PATH_V2`, `SUPERVISOR_PATH_V2` | — | **não suportado no 1º corte** (sem `__file__`, sem subprocesso em E) |
| `git` (produtor) | host | produtor | captura | transporte | — | nenhuma para integridade (hash-on-read) | **dependência de disponibilidade**, não TCB de integridade |
| `pwd` (stdlib) | TCB | `zoneinfo._tzpath` → `sysconfig._getuserbase` → `expanduser` sem `HOME` (pilha registrada) | tardio | resolve home pelo passwd (NSS nativo, `/etc/passwd` root-owned) | — | — | TCB; mostra que o ambiente é entrada: `env={}` muda o caminho da stdlib, sem efeito no resultado (paridade) |

Subprocessos no percurso: **0**; aberturas em checkout/venv durante E: **0**. Ambas as contagens vêm
do audit hook do próprio bootstrap: veem só eventos em nível Python (`open`, `subprocess.Popen`,
`os.system`, `os.spawn`, `os.exec`, `os.posix_spawn`, `os.fork`, `pty.spawn`), não aberturas feitas
por código nativo, e valem sob **P**. Cada caminho é resolvido contra o cwd (`realpath`) e comparado
por componente com as raízes observadas (R2-5); o controle positivo `auditor_detects_relative_checkout_read`
mostra que uma leitura relativa do checkout é detectada. Limite: o evento de `os.open(..., dir_fd=)`
não traz o `dir_fd`.

### 6.2 Piso de TCB proposto

1. Kernel Linux (memfd seals, `/proc`, Yama `ptrace_scope ≥ 1`) e root.
2. Interpretador CPython da versão/ABI dos wheels (`cp311`, manylinux glibc x86_64), stdlib,
   `lib-dynload`, ld.so/glibc/libgcc_s: **não graváveis pelo UID do runner**, invocados por caminho
   absoluto. Autenticá-los (estilo ADR-0012) é endurecimento futuro, não o 1º corte.
3. Launcher + produtor + bootstrap (a própria implementação de S/E): vindos de local não gravável
   pelo UID do runner; sem auto-autenticação recursiva. **Onde** isso vive em produção está aberto
   (U3).
4. Anchor `C`: #319.

"Fica no TCB" não é dispensa: cada item acima nomeia autoridade (ownership/DAC ou #319), forma de
aquisição (instalação root), mutabilidade (nenhuma pelo UID do runner), validação (EXP-BOOT
`floor`, com probe por arquivo — `access(W_OK)` e abertura `O_WRONLY` sem escrita — e por criação só
para diretórios; o probe é discriminado por um arquivo gravável dentro de diretório não gravável,
R2-6: 23 caminhos — interpretador, stdlib, `lib-dynload`, cada `.so` mapeado pelo interpretador,
as libs `NEEDED` do `pydantic_core` resolvidas pelo `ld.so.cache`, e todos os ancestrais — uid 0 e
escrita negada ao ator; **só neste container**) e consequência por target:

| Target | Situação observada | Consequência |
|---|---|---|
| AgentEscala | runner self-hosted `ct104`, sem `setup-python`; venv sobre o `python3` do host; ownership/versão **não observados** daqui | ativação depende de U2 e U3 |
| CAEM | sem workflow AgentReview v2 hoje | se usar `setup-python` em runner hospedado, o toolcache pertence ao UID do runner → piso 2 **não atendido**; não suportado até decisão |
| SACR-AS | sem workflow AgentReview v2 hoje | idem CAEM; fixtures clínicas sintéticas não mudam a engine |

## 7. Obrigações de desenho (IDs locais desta slice)

Formato: proposição · domínio · truth-maker · produtor → consumidor · contramodelo · positivo ·
limite · owner · evidência.

| Grupo | Obrigações | Estado em S0 |
|---|---|---|
| 7.1 — S_G (claim de S0) | 301S-SNAP, PRIV, ID, AUTH, FID, STAB, BIND, RES (commit/tree/blob), LIFE | ratificadas como contrato de componente sob `Applicable_SG` (evidência: head experimental `34fc575`); obrigações sucessoras em §12 |
| 7.2 — E (viabilidade) | 301S-BOOT, LOAD (confinamento observado), CHAN, DATA, CALLER | propostas; E é futura |
| 7.3 — S_D (futura slice) | 301S-DEP, NAT, RES (S_D), completude do loader | `DEFINED`; contramodelos obrigatórios R2-1/2/3/7/8 |

### 7.1 — S_G (claim de S0)

- **301S-SNAP** — S_G é adquirido só de um snapshot físico publicado por um produtor com separação
  de privilégio, autocontido, sem metadata da fonte, e o Git nunca lê o repositório vivo na captura ·
  repositórios Linux sob uma capability de storage admitida · cópia física por descritor (G1C, sem
  Git, sem inflate); esqueleto e `config` do produtor; staging → finalize → `rename` como commit
  point · produtor → leitor · fonte com remote, promisor + `extensions.partialClone`, `hooksPath` +
  hook, alternates, refs e `tmp_obj_*`; alternate fora da capability; objeto obrigatório ausente;
  SIGKILL do produtor durante o staging; loose-bomba de 256 MiB · nada da fonte atravessa (C2);
  `alternate_outside_authorized_storage` (C9); `object_missing` sem marcador de busca (C4/C8B); 0
  snapshot publicado e staging ilegível pelo runner (C10); produtor sem inflate, VmHWM 21,7 MiB (C5);
  alternate autorizado achatado sem ponteiro, clone parcial com closure completa **admitido**,
  sha256 aceito; toda entrada **listada** (copiada ou ignorada) conta no orçamento de entradas
  (`C5_producer_listing_charged_to_entry_budget`), **inclusive a listagem da sonda estrutural de um
  alternate**
  (K3: sonda experimental com o mesmo significado da G1C — `HEAD` irmão, `pack/`, `info/` em O(1), e
  a busca de fanout passando por `scanned()`; alternate com 3.000 entradas-lixo antes do fanout e
  orçamento 1.000 → `physical_budget_exceeded` com 1.001 entradas enumeradas, contra 4.002 na ablação
  com a sonda da G1C; pool pequeno aceito). K3 limita **a listagem de diretórios**, não toda a
  família de alternates: a leitura do manifesto (até 64 KiB, truncado em silêncio acima disso) e a
  resolução de ponteiros não são cobradas — X2/T-F4, 11.903 `open` contra 23 entradas cobradas;
  S1_RES_01. Escritas curtas forçadas (4.093 B por `write`) publicam
  bytes idênticos à fonte e um recibo igual ao destino, e a ablação "uma escrita por bloco" publica
  bytes truncados (RC-5) · quem autorizou a capability é #331/C2_B; produtor de produção U3; GC do
  staging é obrigação futura; a sonda de produção da G1C **não** foi alterada (S1 exige uma sonda
  limitada aprovada pelo owner); R4-3: um pack maior que o envelope do leitor torna S_G
  indisponível — `ACCEPTED_S0_LIMITATION_REQUIRES_S1_PARAMETER_DECISION`, sem efeito sobre
  autenticidade · #301 (S_G), #331 · EXP-ARCH-C C2/C4/C5/C8/C9/C10/K3/RC5, `POSITIVE_sha256_runner_S_G`.
- **301S-PRIV** — `RunnerCanRead(Snapshot) ∧ ¬RunnerCanMutate(Snapshot)` **dentro de um
  `AuthorizedReaderExecutionContext`** (precondição externa do host/launcher); o leitor roda como o
  runner e aplica checagens de defesa em profundidade no consumo · snapshot publicado · negação do kernel (DAC no experimento) +
  precondição do leitor · produtor → leitor · 12 mutações pelo runner (escrever `config`, criar
  alternates, reescrever/renomear loose, reescrever pack, `unlink`, `rename`, `mkdir`, `chmod` do
  snapshot e de objeto, renomear o snapshot, criar irmão em `committed/`); cópia do snapshot de
  posse do runner; snapshot root-owned sob diretório do runner, acessado por symlink root-owned, por
  caminho direto e por caminho relativo; formato esperado divergente; leitor com uid efetivo do dono
  do snapshot (K1A), com uid salvo do dono (K1B), com capability permitida e `CapEff = 0` (K1C) ·
  todas EACCES/EPERM, leitura
  positiva ok (C1); `snapshot_mutable_by_reader` (cópia do runner; ancestral real do runner);
  `snapshot_path_not_canonical` (symlink, relativo), com o contramodelo confirmado (o runner renomeia
  o snapshot); `snapshot_format_mismatch`; `reader_principal_mismatch` (K1A, K1B) e
  `reader_has_capabilities` (K1C), cada contramodelo **exercitado** (o processo altera o snapshot com o
  que possui); principal estabelecido (uid/gid 2000 nos quatro campos, capabilities 0) aceito sobre um
  snapshot de um produtor não-root (uid 3000); leitor com uid
  2000 deriva a estrutura declarada (árvore vazia, executável, symlink com target em bytes,
  subárvore) e o S_G do toolrepo real · a propriedade é do kernel no domínio do experimento
  (container sem userns-remap); CT104 e o serviço de produção não testados · #301, U3 ·
  EXP-ARCH-C C1/C3/C4/C11/K1.
- **301S-ID** — S carrega `subject_identity = (algoritmo, C)`, `container_digest` e (para S_D)
  `dependency_identity` como fatos distintos; a **única** identidade de entrada é `expected_subject
  {object_format, commit_oid, component_policy}`, e o algoritmo aceito é o dela, não o rótulo do
  container nem a config viva. A raiz é **derivada** dos bytes autenticados de `C`; não há autoridade
  independente de root_tree. A revalidação pós-selo **re-deriva** S_G do mapa de objetos endereçado
  por conteúdo e compara com o conteúdo selado (substitui o registro de aquisição em memória de B,
  cuja falsificação coerente passava: B-4). O recibo do snapshot serve só para rastreabilidade
  capability → snapshot → S_G · captura/handoff · cabeçalho selado + digest · produtor → leitor →
  bootstrap · bytes certos e commit errado; commit certo com rótulo de algoritmo incoerente (R2-4);
  registro auxiliar apontando outra tree real; entrada forjada no mapa · identidade correta aceita ·
  a integridade do processo leitor continua premissa (**P**) · #301 · EXP-CAPTURE
  `binding_right_bytes_wrong_subject_identity`, `binding_right_commit_wrong_algorithm_label`;
  EXP-ARCH-C C6 (raiz derivada; ablação `root_override` muda S_G; `object_map_binding_mismatch`).
- **301S-AUTH** — todo objeto que contribui para S é autenticado no consumo · commit/tree/blob,
  sha1 e sha256 · hash-on-read sobre o buffer usado · produtor · troca de objeto commit, tree ou
  blob após a aquisição · bytes legítimos aceitos; restaurar volta a aceitar · pack/alternates só
  por argumento; sha1 sob a premissa de segunda pré-imagem, sem equivalência sha1dc (§2) · #301 ·
  EXP-N1 (21 casos, inclui ablação e testemunha herdada; positivos sha1 **e** sha256). Nenhum teste
  pretende provar resistência a segunda pré-imagem.
- **301S-FID** — S preserva as distinções de contract A/A2 e recusa o resto explicitamente ·
  árvores Git suportadas por C3 · paridade com árvore declarada **e** com o enumerador C3 ·
  produtor · 7 contramodelos (diretório vazio ±, aninhado, exec bit, target de symlink, tipo,
  vazio→arquivo) · paridade exata · nomes não-UTF-8 aceitos como bytes pelos dois · C3 (regra) /
  #301 (uso) · EXP-STRUCT (mutante com perda colide em 5/7). O protótipo **espelha** regras que o
  C3 já possui no builder hierárquico (duplicado, round-trip `fsdecode/fsencode`, ciclo,
  profundidade ≤ 100, limite pré-leitura `size // 295`); S1 deve obtê-las do próprio C3, não
  dessas cópias. O limite de componente é **parâmetro explícito de admissão** (sem default; ausência
  recusa): a paridade com C3 só é afirmada quando os dois recebem o mesmo limite (EXP-STRUCT: 256
  bytes admitido com 300, recusado com 255 — por S_G e pelo C3; `no_admission_limit_refused`).
- **301S-STAB** — após compromisso, S é imutável para não-root, e o que foi comprometido é o que
  foi autenticado · memfd; observado só no kernel 6.18/WSL2 · selos + re-hash pós-selo +
  **revalidação do objeto selado** contra o registro da aquisição · produtor → todos · bateria de 9
  operações de escrita por outro processo; escritor pré-selo; selo estranho; mapeamento gravável
  retido; **autenticar A e entregar B antes da serialização**; nó fora do registro · conteúdo
  idêntico, compromisso sem atacante · disponibilidade não coberta · #301 · EXP-CAPTURE (ablação
  sem revalidação compromete B).
- **301S-BIND** — o consumidor só aceita o descritor recebido se selado, regular, com digest e
  identidade esperados vindos do launcher · handoff · `open_sealed` sobre o buffer único ·
  launcher → bootstrap · arquivo regular idêntico, memfd atacante, sem `F_SEAL_WRITE`, sem identidade
  esperada, fd não herdado · handoff correto · número de FD em receipt não é prova · #301 ·
  EXP-CAPTURE `binding_*`.
- **301S-RES (S_G)** — a captura de S_G é limitada antes da expansão, com **dois domínios de
  orçamento que não se misturam nem se inferem um do outro**: `snapshot_budget` (físico: bytes,
  entradas, profundidade de alternates; cobrado pelo `fstat` antes da leitura, no produtor, que
  nunca infla) e `closure_budget` (subject: nós, payload, metadados, paths, componente; cobrado pelo
  cabeçalho estrito do transporte, no leitor) · por ocorrência, para commit, tree **e** blob; **prazo
  por objeto** no leitor (ablação sem prazo: a captura trava num objeto malformado) · (i) o filho
  `git` roda num envelope de memória do kernel por processo (`RLIMIT_AS`, aplicado antes do `exec`);
  (ii) o cabeçalho é lido com limite e parseado estritamente; (iii) todo corpo é cobrado a partir
  desse cabeçalho antes de ser lido (commit: teto próprio; tree: limite pré-leitura do C3 com o
  orçamento restante e orçamento de metadados; blob: orçamento de payload); (iv) toda recusa mata e
  colhe a unidade · produtor + leitor + kernel · filho sem contenção com pai lento vai a 68 MiB,
  contido fica em 11 MiB (envelope de teste 64 MiB); 9 cabeçalhos hostis; subárvore compartilhada
  2.000×; blob de 32 MiB; tree de 16,7 MB; commit de 16 MiB; profundidade 110; contagem cumulativa de
  nós; loose-bomba fora da closure (S_G aceito, produtor sem inflate) e dentro da closure
  (`budget_payload_bytes`, unidade Git ≤ 14,8 MiB); **no leitor de C**: blob de 1 MiB em 100 paths
  com orçamento de 8 MiB → `budget_payload_bytes` (cada ocorrência repetida é cobrada do corpo já
  autenticado antes do uso; controle com 4 paths aceito) e ~18,6 MB de paths → `budget_path_bytes`
  (`charge_node` durante a caminhada); **o parser de cada tree recebe o restante** do orçamento de nós,
  e não uma constante global (K4: `TreeParserExpansion <= RemainingClosureNodeBudget`; restante ≤ 0
  recusa antes de carregar a tree; 992 nós consumidos sob `max_nodes` 1.000 e uma tree compacta de 50
  entradas → `budget_nodes` com teto 8; o parser do C3 aplica o teto incrementalmente e materializa
  no máximo restante+1 entradas antes de recusar (o contador "além do restante" do experimento mede
  listas completas, 0), contra 50 na
  ablação com o teto global; mesma forma com 5 entradas aceita) · corpus real aceito com a unidade Git ≤ 14,8 MiB
  dentro do envelope de 128 MiB · valores de envelope e orçamentos (§8) ainda não adjudicados;
  memória **agregada** da unidade e prazo total **não** reivindicados (#320); `RLIMIT_AS` segue a
  semântica já usada por `trusted_check_supervisor_v2` · #301 · EXP-RES `git_child_contained_*`,
  `strict_transport_*`; EXP-ARCH-C C5 (inclusive `C5_blob_charged_per_occurrence_refused`,
  `C5_path_bytes_budget_enforced_during_walk`), K4; EXP-FUNC. O leitor de `34fc575` converte
  **toda** recusa do C3 numa tree abaixo da raiz em `budget_nodes` (`..`, `a/b`, tree truncada ou
  malformada: T-F3), não só o estouro de entradas. A decisão de recusar está certa; a causa
  reportada está errada quando a causa autoritativa é conhecida (S1_SEM_01:
  `RefusalDecisionAuthority = RefusalCauseAuthority`). Os casos de EXP-RES exercitam o caminho
  `build_subject`, **não** o leitor de C; só os casos de EXP-ARCH-C valem para C.
- **301S-LIFE** — cada descritor tem um dono; falhas não produzem S parcial nem snapshot publicado;
  filho sem resposta não vira sucesso; nada do transporte sobrevive à captura · produtor/leitor/
  launcher · FD/processos contados antes e depois; o leitor é *child subreaper* e o teardown da
  unidade mata e colhe **todos** os descendentes; o leitor confere que é subreaper antes de iniciar o
  Git (`subreaper_required`) e um descendente que sobreviva ao prazo do teardown vira recusa
  (`unit_teardown_incomplete`) em qualquer desfecho **depois do `try`**, sucesso ou falha, com
  precedência sobre a
  falha primária, que fica registrada como diagnóstico experimental (K2: transporte que falha +
  teardown que reporta sobrevivente → `unit_teardown_incomplete`, falha primária
  `transport_header_invalid`; a ablação "só no sucesso" esconde o sobrevivente). Não cobre a janela
  entre o spawn do Git e o `try` (T-F5: uma exceção de construção deixa o Git vivo) nem um `kill`
  com EPERM depois de ganho de privilégio no `exec` (X1: `PrivilegeGainCanBreakKillAuthority`) —
  S1_LIFE_01 e S1_CTX_01. Staging só vira snapshot no commit point · — ·
  ferramenta falsa → filho → neto com `setsid` → `sleep` infinito; SIGKILL do produtor no meio do
  staging; falha de escrita (EFBIG), de selo (injetada), de hash no meio da captura, falha do
  launcher antes do spawn, filho travado, filho com rc 0 sem resposta · unidade: `transport_deadline`
  e 0 sobreviventes (C7), **ablação** "só grupo de processos": o neto `setsid` sobrevive (limpo depois
  por pid + starttime + nonce); FDs restaurados; censo final sem processos nem sockets em escuta ·
  lixo de staging após crash exige GC futuro; descendente real em sono não interrompível (estado D)
  **não testado**: o ramo lógico é discriminado com um teardown stub (K2); #354 não é ativado (§8) · #301 · EXP-RES, EXP-ARCH-C
  C7/C10/K2/`LIFECYCLE_*`.

### 7.2 — E (viabilidade; E é futura)

- **301S-BOOT** — nenhuma configuração controlada por outro ator age antes do 1º import ·
  launcher · interpretador root-owned absoluto, `env={}`, `-I -S`, `cwd=/` · launcher · `pyvenv.cfg`
  editado (executa sob `-I -S`), `LD_PRELOAD`, `PYTHONPATH`, módulo no cwd, `.pth` de user site ·
  controles sem payload · observação por marcador externo, não autorrelato · #301 · EXP-BOOT.
- **301S-LOAD** — semântica de import explícita · E · top-level: S só para suas raízes {`app`},
  antes do `PathFinder` (espelha `PYTHONPATH`); S_D depois (stdlib precede site-packages).
  Submódulos: cada finder só responde dentro de pacotes que ele próprio criou (o `__path__` desses
  pacotes contém apenas o marcador do finder), como o `__path__` confina o `FileFinder`. Ordem por
  nome: diretório com `__init__.py` > extensão (só D) > fonte `.py` > namespace PEP 420 de nó
  `tree`. Symlink em S é dado, **não** caminho de import; nenhum `.pyc` lido/escrito; nenhuma
  distribuição visível · bootstrap · fallback a checkout/venv/pyc; D tentando estender `app`,
  `json`, `encodings` ou sombrear `json` · 0 aberturas em checkout/venv; mesmo resultado; injeções
  recusadas · **não suportado, explicitamente**: `__file__`, `inspect.getsource`,
  `importlib.resources`, `pkgutil.iter_modules` sobre pacotes de S/D (a engine não os usa hoje;
  proibir um caso necessário exige decisão de produto) · #301 · EXP-FUNC, EXP-CAPTURE.
- **301S-CHAN** — o resultado só chega por canal não reabrível por terceiros · socketpair herdado ·
  ENXIO ao reabrir via `/proc` · bootstrap → launcher · pipe reaberto injeta resultado forjado ·
  resultado genuíno · o conteúdo ainda é autorrelato do filho (vale sob **P**) · #301 · EXP-PROC.
- **301S-DATA** (aberta) — dados do target consumidos por E não retornam a bytes mutáveis · hoje
  #200-G4B relê por path e declara same-UID fora de escopo · owner a decidir (#301-E / #331) · U4.
- **301S-CALLER** (aberta) — onde roda o adapter do target e como o Router é chamado sem pôr
  código do target no processo de E · owner #301-E · U1.

### 7.3 — S_D (futura slice; `DEFINED`, não qualificadas por S0)

Contramodelos **obrigatórios** herdados da rodada 2 (PR #355, comentário 5852251027):
- R2-1: `METADATA`/`WHEEL`/`RECORD` inflados antes da cobrança (reproduzido: 2.282 MiB / 1.001 MiB com
  orçamento de 8 MiB);
- R2-2: arquivo do wheel lido inteiro sem limite;
- R2-3: `DT_FILTER`/`DT_AUXILIARY` não inspecionados;
- R2-7: pacote-extensão `pkg/__init__.so` não procurado;
- R2-8: superconjunto de tags no `WHEEL` aceito.

Mecanismo proposto (spike descartável, não implementado):
[`experiments/sd_future/spike_bounded_archive.py`](experiments/sd_future/spike_bounded_archive.py) —
um único leitor de arquivo com cobrança pelo qual passa toda leitura. Reprodução do defeito no
protótipo congelado: [`experiments/sd_future/repro_r2.py`](experiments/sd_future/repro_r2.py). O
protótipo `s0_deps.py` fica **congelado** no estado de `1e2453e`, com esses defeitos conhecidos.

- **301S-DEP** — dependências vêm só de S_D, vinculado ao lock (nó regular) dentro de S_G · lock
  atual · sha256 do wheel ∈ lock, identidade interna e tags, RECORD, tamanho antes de inflar,
  colisões, vínculo `sha256(lock)` e tag do interpretador no cabeçalho · produtor → finder de E ·
  25 casos sintéticos (renomeado, tags, ABI, duplicado, RECORD, `.pth`, `.data`, `..`, symlink,
  colisões, hash/lock ausentes, lock duplicado/marker/hash em comentário, zip bomb de 200 MiB) + D de
  outro lock, D ausente, venv adulterada, plugin plantado · controle sintético aceito e paridade
  funcional · a checagem do consumidor é de **rótulo** (digest do lock e tag); a autenticação por
  membro aconteceu no produtor (TCB) · futura slice S_D · EXP-DEPS e EXP-FUNC observam o protótipo;
  limitação conhecida R2-1/R2-2/R2-8.
- **301S-NAT** — extensões nativas executam só bytes de S_D, e sua closure dinâmica fica no TCB ·
  `.so` do lock · ELF inspecionado pelo produtor; memfd selado criado e re-hasheado **no filho**,
  `ExtensionFileLoader` em `/proc/self/fd/N` · produtor → bootstrap · `.so` com `RPATH`, `RUNPATH` ou
  `DT_NEEDED` absoluto · `.so` sem caminhos aceito; `pydantic_core` e `yaml._yaml` carregados de
  memfd · as sonames resolvem pelo `ld.so.cache` root-owned; `LD_*` fechado pelo launcher · futura
  slice S_D · EXP-DEPS `native_*`, EXP-FUNC census; limitação conhecida R2-3/R2-7.
- **301S-RES (S_D)** — ingestão de S_D limitada antes de qualquer expansão (arquivo, diretório
  central, todo membro inclusive metadados) · **REFUTADA** no protótipo em `1e2453e` (R2-1/R2-2) ·
  futura slice S_D, com o mecanismo do spike.

## 8. Recursos e lifecycle

Medidos no corpus real (`C = 9abcde64`) em CPython 3.11.16 / git 2.39.5 / tmpfs, captura pela
arquitetura C (produtor uid 0 do container, leitor uid 2000; EXP-FUNC e EXP-ARCH-C); números valem
**para este ambiente/corpus**, não são tetos universais (K06 de #324):

| Vetor | S_G | S_D | E (filho) |
|---|---|---|---|
| Entradas lógicas | 1.001 nós (206 tree, 776 regular, 19 exec, 0 symlink, 0 vazios) | 186 nós = 164 membros + diretórios | 35 módulos de S, 66 de S_D, 2 nativos |
| Bytes únicos vs por ocorrência | 8.642.394 (blobs únicos); 51.346 B de corpos commit/tree | 9.700.542 (payload) | — |
| Bytes de paths (soma dos comprimentos) | 54.726 B | — | — |
| Container (RAM-backed) | 8.769.133 B | 9.718.908 B | 2 memfds nativos no filho |
| Snapshot físico (arquitetura C, domínio `snapshot_budget`) | 3.646.095 B em 2 entradas (1 pack + idx), 0 alternates; produtor uid 0: heap 3,81 MiB, VmHWM 23,3 MiB, rchar 5.632.037 / wchar 3.646.583, 0,061 s, 0 processos Git | — | — |
| Closure (domínio `closure_budget`, leitor uid 2000) | 933 objetos únicos lidos 1× cada; payload cobrado por ocorrência 8.660.242 B; heap do leitor 36,69 MiB, VmHWM 58,2 MiB, 0,295 s | — | — |
| Heap de pico build → selo → revalidação | 34,7 MiB (EXP-FUNC) | 54,2 MiB | maxrss 88.608 KiB (normal: 88.608) |
| Memória da unidade `git` local | ≤ 14,8 MiB (limite superior medido), envelope `RLIMIT_AS` 128 MiB **por processo**; agregado **não testado** (sem cgroup) | — | — |
| FDs | 1 por container | 1 | 3 herdados (por construção) + 1 socket + 2 nativos |
| Subprocessos | 1 unidade `git cat-file` sobre o snapshot (formato conferido na config do produtor, sem `rev-parse`); 0 no produtor | 0 | 0 (audit hook) |
| I/O | 933 objetos únicos do `git` (payload 8.642.394 B + metadados 51.346 B) | 6 wheels lidos 1× | 0 aberturas em checkout/venv |
| Tempo | snapshot 0,061 s (processo separado); build 0,170 s; serialize+selo+revalidação 0,102 s | 0,099 s | 0,39 s (normal 0,25 s) |

**Dois domínios de orçamento** (não se misturam nem se inferem um do outro): `snapshot_budget`
proposto de 256 MiB, 200.000 entradas, profundidade de alternates 8, proporcional ao **store** e
cobrado pelo `fstat` antes da leitura (a loose-bomba de 256 MiB inflados é cobrada pelos 261.293 B
físicos e **nunca** inflada pelo produtor: EXP-ARCH-C C5; toda entrada listada conta) — e
`closure_budget` do subject (abaixo). Os dois valores ainda **não** são coerentes entre si: um
snapshot de até 256 MiB é admitido, mas um pack maior que o envelope de 128 MiB do Git local não é
mapeado e torna S_G indisponível (revisão de `a858dc9`, F3; R4-3), com uma razão de recusa
enganosa (`transport_header_invalid`). Disposição do mantenedor:
`R4_3: ACCEPTED_S0_LIMITATION_REQUIRES_S1_PARAMETER_DECISION` — afeta disponibilidade, não
autenticidade nem a claim de identidade de S0. S0 **não** afirma que todo snapshot admitido é legível
sob o `RLIMIT_AS` atual; S1 decide a coerência admissão física ↔ envelope do Git local, ou uma
política de recusa proporcional (§12 (i)). **Prazo** proposto por objeto no leitor: 30 s (#320 é owner da família de timeouts
do Git local). Envelope **proposto** do transporte local: `RLIMIT_AS` = 128 MiB (a base do `git cat-file` é ~8 MB de
VM; o envelope precisa admitir o maior corpo que a política admite). Limites **propostos** (para
adjudicação), aplicados antes da expansão: `max_payload_bytes` (blobs,
por ocorrência) = 64 MiB (≈7× o corpus atual; memfd é RAM/shmem, então o orçamento de disco de C3
de 2 GiB **não** se transfere); `max_metadata_bytes` (corpos commit+tree) = 64 MiB;
`max_commit_bytes` = 1 MiB; `max_path_bytes` = 16 MiB. Para S_D, um teto de 64 MiB é **proposto e
não imposto** pelo protótipo (R2-1/R2-2): fica com a futura slice e o mecanismo do spike. Valores que pertencem ao C3 e devem vir dele: entradas 100.000, profundidade 100,
componente 255, limite pré-leitura `size // 295`. Todo corpo é cobrado pelo header do `cat-file`
antes de ser lido (EXP-RES: blob de 32 MiB, tree de 16,7 MB e commit de 16 MiB recusados com heap
de 0,06 MiB; EXP-DEPS: um membro-bomba comum de 200 MiB é recusado com 0,27 MiB, mas `METADATA`/`RECORD`-bomba não —
R2-1). O memfd é contabilizado como shmem no memcg do processo;
comportamento sob limite de memcg **não testado**.

Ownership: produtor possui o memfd até retornar; em falha fecha uma vez e propaga. Launcher possui
as cópias herdadas até o filho terminar; o filho possui os memfds nativos que cria. Resultado ausente
(timeout, rc 0 sem resposta, canal malformado) nunca é convertido em resultado válido.

**#354.** Os sítios com a forma `open(next, dir_fd=cur)`/`close(cur)` estão em
`_open_directory_relative_v2` (G1/Q) e, em C3, em `_fd_rmtree` e na materialização
(`git_commit_subject_v2.py` ~300, ~1516, ~1828). O percurso de S usa `_parse_tree_data` (puro) e
`git cat-file`; bootstrap e launcher não abrem diretórios. O produtor da arquitetura C abre
diretórios do storage com as primitivas da G1C (`_open_dir_no_follow_v2` fecha cada fanout no
`finally`; alternates passam por `_open_dir_by_segments_no_follow_v2`, cuja contabilidade de fd já
foi corrigida na G1C) — não pelo sítio `_open_directory_relative_v2`. **S não ativa #354.** Se uma
implementação futura reutilizar materialização ou a verificação Q no percurso de E, #354 passa a ser
obrigação do consumidor.

## 9. Interface conceitual S → E (sem implementar E)

```text
entrada admitida (C via #319, lock ∈ C) + política (orçamentos, raízes) + ambiente (piso §6.2)
  → captura autenticada (301S-AUTH/FID/RES)            [pode falhar; nada sai]
  → compromisso: S_G, S_D selados + digests            [301S-STAB]
  → handoff: descritores herdados (pass_fds) + identidade esperada no argv do launcher
             (digests, C) + socketpair de resultado; interpretador root-owned absoluto, env={}, -I -S
  → validação no bootstrap: regular ∧ selado ∧ digest ∧ (algoritmo, C) ∧ vínculo S_D↔lock   [301S-BIND/ID]
  → carga: finder fechado sobre S_G/S_D; nativos → memfd selado no filho       [301S-LOAD/NAT]
  → resultado pelo socketpair; ausência ≠ sucesso                              [301S-CHAN/LIFE]
```

- **Vínculo S_D ↔ S_G no consumidor é de rótulo**: confere `sha256(lock)` e a tag do interpretador
  gravados no cabeçalho selado de S_D; a autenticação por membro foi feita pelo produtor, sob a
  premissa de TCB do launcher.
- **Descritor recebido ≠ número de FD num receipt.** O que o consumidor validou é o objeto que o
  kernel entregou; um número escrito num receipt não identifica objeto algum depois do close.
- **Origem das observações.** Fatos que o launcher produziu (digest calculado, fds passados, argv,
  env, interpretador) são dele. O que o bootstrap observa (módulos por origem, aberturas, dlopen)
  vale sob **P** e termina a regressão no piso de §6.2. PID, `module.__file__`, `sys.flags` e
  mensagens do filho **não** provam o próprio bootstrap (ADR-0012 como referência; EXP-BOOT mostra
  código rodando antes do `-c` enquanto `sys.flags` seguiria correto).
- **Symlink preservado ≠ symlink importável.** S_G preserva symlinks como dados; o loader recusa
  importar através deles. Namespace packages vêm de nós `tree`. Relançamento de processos a partir
  de E e `importlib.resources` não fazem parte do 1º corte e exigirão semântica própria.
- **Não reconstruir S a partir de M.** E nunca abre checkout, venv ou M.

Perguntas **abertas que bloqueiam E** (não S1):

- **U1 — posição do adapter/Router.** No caller real, código base-owned do target é `__main__` do
  processo da engine e chama a rede no meio do pipeline. Opções: (a) E expõe fases da engine com
  E/S só de dados (build → Router fora de E → consume); (b) o adapter vira um segundo subject
  autenticado a partir do commit base do target. Decisão de produto; #301-E.
- **U2 — interpretador nos runners.** Versão/ABI/ownership do `python3` de CT104 não observados;
  CAEM/SACR-AS sem workflow. Sem isso o piso §6.2-2 é premissa, não fato.
- **U3 — proveniência do launcher/produtor/bootstrap.** Hoje a única cópia da engine no runner é o
  checkout gravável pelo UID do runner; o código que constrói S não pode vir dele. Exige instalação
  root-owned ou equivalente; pode interseccionar #331 (autoridade de storage do host).
- **U4 — canal de dados do target.** `external_path_ingress_v2` relê por path (2 aberturas por
  arquivo) e exclui same-UID do seu escopo. E precisa receber esses dados como bytes/descritor ou
  declarar a exclusão.

## 10. Alternativas consideradas

- **F — filho autentica direto do repositório antes do import** (spike): rejeitada; move parse
  Git para o TCB pré-import e mistura WHAT/HOW sem ganho, pois o launcher é TCB de qualquer forma.
- **memfd por arquivo:** rejeitada; ~800 FDs para o corpus atual contra 1 por container.
- **Alternativa material comparada — dependências por instalação root-owned (DAC) em vez de S_D.**
  Resolve 301S-DEP se o runner for provisionado por root e a venv não for gravável pelo UID do
  runner; perde o vínculo criptográfico `lock ∈ C → wheel → membro` (autoridade passa a ser o
  provisionamento, não o commit) e não fecha plugins/`.pth` se `site` rodar. S_D fica como proposta;
  a alternativa é compatível como piso de TCB se U3 for resolvido por provisionamento.
- **Autenticar também interpretador/stdlib (ADR-0012 `python-runtime` snapshot):** endurecimento
  posterior; exige launcher nativo e namespace de montagem; fora do 1º corte.

## 11. Respostas ao preflight §§1–7

1. **Propriedade.** S_G-CLAIM (§2). Observação mecânica: hash-on-read por objeto; cobrança de todo
   corpo pelo header; paridade estrutural; `F_GET_SEALS` + re-hash pós-selo; validação do descritor
   recebido e da identidade `(algoritmo, C)`. Paridade funcional e 0 aberturas em checkout/venv são
   viabilidade de E, não parte da claim. Disposição conservadora: qualquer falha recusa e
   **nenhum** S parcial é emitido; resultado ausente não é sucesso.
2. **Autoridade.** Fronteira de aquisição → arquitetura C (ratificada como contrato de componente; A e B rejeitadas): a
   imutabilidade do snapshot é autoridade do **kernel** sobre um produtor com separação de
   privilégio (DAC no experimento); a abertura do storage por descritor dentro da capability reusa
   primitivas da G1C (`trusted_object_authority_v2`) **sem** o inflate de loose, `verify-pack` ou
   `rev-parse` — uma variante só-física exige decisão do owner da G1C. Autorização do host para a
   capability → #331/C2_B; proveniência do produtor em produção → U3; timeouts/recursos do Git local
   → #320. Formato de objeto Git (hash por tipo/tamanho/conteúdo) → S **deriva** dele; o formato
   aceito vem do `expected_subject`.
   Regras de árvore → C3: `_parse_tree_data` (usada diretamente pelo protótipo) e o builder
   hierárquico `_build_canonical_trie_hierarchical` (duplicado, round-trip, ciclo, profundidade 100,
   componente, limite pré-leitura). O protótipo **espelha** estas últimas — são cópias, portanto hoje
   há duas instâncias dessas regras no protótipo. S1 deve ter **uma**, separando **aquisição** de
   **interpretação estrutural** (decisão b): S1 é dono de quais bytes foram adquiridos (transporte
   contido), autenticados e comprometidos; o C3 continua dono do que uma tree significa, exposto como
   interpretação sobre bytes **fornecidos** pela aquisição. A proposta anterior ("inserir hash no
   leitor do C3 e consumir o carrier") foi **retirada**: o carrier de blobs do C3 grava num spool
   temporário reabrível e relê dele, o que reproduz "verificar uma leitura, consumir outra" (R3-4).
   Nenhum segundo parser estrutural é criado. Limite de componente → parâmetro explícito de admissão;
   paridade com C3 só sob o mesmo limite (decisão d). Autorização de wheels → lock em `C`; formato
   de wheel → PEP 427/RECORD/METADATA/WHEEL (derivados). Anchor → #319.
3. **Linguagem/capacidade.** Aceita: objetos sha1 (sob a premissa de segunda pré-imagem, sem
   equivalência sha1dc) e sha256; modos `040000/100644/100755/120000`; nomes em bytes até o limite
   de componente **explicitamente** admitido; wheels puros e nativos cp311 manylinux sem RPATH/RUNPATH. Recusa explícita:
   gitlink, modos não canônicos, `..`, duplicados, captura sem limite de componente, componente
   acima do limite, cabeçalho de transporte fora da forma estrita, lock não regular, lock com duplicado/marker,
   identidade/tag de wheel divergente, `.pth`, `.data/`, symlink em wheel, colisão arquivo/diretório,
   nativo com caminho de busca, plugins pydantic, import através de symlink, `__file__`,
   `inspect.getsource`, `importlib.resources`, `pkgutil.iter_modules`, relançamento por path.
   U1–U4 são perguntas de E; U3 também condiciona o **significado operacional** de S1 (§12).
4. **Corpus.** Negativo: EXP-N1, EXP-STRUCT §4, EXP-CAPTURE, EXP-BOOT, EXP-RES. Positivo **com
   igualdade**: paridade com árvore declarada, com C3 (`list_commit_tree_structure_v2` +
   `read_commit_blobs_v2`) e com o resultado da engine no caminho normal. Limite: a paridade com C3
   foi medida só no corpus sintético (o corpus real, 1.001 nós, não tem symlinks nem árvores vazias e
   não foi comparado ao C3) — exigida no aceite de S1.
5. **Evidência/mutação.** Mutantes executados e observados: verificação de hash desligada
   (aceita blob adulterado), hash-então-relê (incorpora outros bytes), sem re-hash pós-selo
   (compromete conteúdo adulterado), projeção com perda (colide em 5/7). O finder anterior (head
   `3d426e1`, que ignorava `path`) funciona como mutante de 301S-LOAD: a revisão reproduziu nele a
   injeção que o finder corrigido recusa. Predicados admitidos (vocabulário do preflight):
   `DEFINED`, `MECHANICALLY_VERIFIED` e `EMPIRICALLY_SUPPORTED` no domínio/corpus declarados;
   `MUTATION_DISCRIMINATED` para 301S-AUTH/FID/STAB (inclusive a revalidação pós-selo: a ablação
   compromete B) e para a contenção de 301S-RES (S_G) (o transporte sem envelope, com pai lento, é o
   mutante: 68 MiB contra 11 MiB). Arquitetura C: `MUTATION_DISCRIMINATED` para a raiz derivada de
   301S-ID (a ablação que confia no registro auxiliar muda S_G: C6), para o teardown de 301S-LIFE (a
   ablação "só grupo de processos" deixa o neto `setsid` vivo: C7) e para a precondição de 301S-PRIV
   (o contra-controle de posse do runner é recusado: C11); a negação do kernel em 301S-PRIV/SNAP
   (C1/C3/C4/C10) é `EMPIRICALLY_SUPPORTED` só neste container; os cabeçalhos estritos são
   `MECHANICALLY_VERIFIED` no corpus declarado. S_D: `DEFINED`; 301S-RES
   (S_D) é `REFUTED` no protótipo `1e2453e` (R2-1). Os instrumentos de medição também são
   discriminados: a auditoria de aberturas e o probe do piso têm controles positivos que os métodos
   da rodada 2 teriam falhado (R2-5, R2-6). `PROVED` não.
6. **Premissas entre camadas.** "git serve os bytes do oid" → **falso** (EXP-N1 HOR), por isso
   hash-on-read. "Um snapshot privado e sem remoto não muda o comportamento do Git" → **falso** se o
   runner pode escrevê-lo (B-1/B-2), por isso a separação de privilégio e a recusa do leitor diante de
   um snapshot mutável por ele (C1/C11). "Matar o grupo de processos mata a unidade" → **falso** para
   um descendente com `setsid` (C7 ablação), por isso o subreaper. "O cabeçalho do `git` é bem formado" → falso sob pipe reabrível (R3-2), por isso parse
   estrito. "Recusar pelo cabeçalho limita a expansão" → falso: o filho expande sozinho; "matar
   rápido" é corrida, por isso envelope do kernel (R3-1). "`-I -S` isola o startup" → falso para `pyvenv.cfg`/`LD_PRELOAD` (EXP-BOOT), por
   isso interpretador root-owned + `env={}`. "O lock autentica a venv" → falso (EXP-FUNC). "Yama
   impede ptrace" → verdadeiro neste host (`ptrace_scope=1`), premissa em CT104 (U2).
7. **Snapshot/ownership.** O produtor é dono da imutabilidade (publica ou nada); o leitor, como o
   runner, é dono da derivação de S_G; o recibo do snapshot é rastreabilidade, não prova. Uma
   decisão, um buffer: cada objeto é hasheado e incorporado da mesma
   leitura; o consumidor valida e parseia o mesmo `pread`. Nenhum outro consumidor relê M em E.
   Segunda cópia de regra: checagem de nome duplicado (acima). Dados do target: **duas leituras por
   path** (U4) — fora de S, registrado.

**Unknown material:** nenhum fato desconhecido para a claim de S_G nem para a propriedade de
componente de S1; U3 condiciona o que o resultado de S1 significa em operação e está declarado como
precondição (§12). U1–U4 bloqueiam E/ativação; S_D tem obrigações `DEFINED` e owner futuro.

## 12. Handoff — menor implementação seguinte

**S1 — captura autenticada e selada do subject Git (WHAT apenas).**

- Entradas: uma **capability de storage admitida** (tipo C2_A; quem autoriza as raízes é #331),
  `repo_root` (locator dentro dela), o `expected_subject {object_format, commit_oid,
  component_policy}`, um **produtor com separação de privilégio** já implantado (U3/#331) e o
  diretório de publicação dele, `snapshot_budget` físico,
  limites de admissão **explícitos** da closure
  (inclusive o limite de componente; quando composto com C3, o mesmo valor que o C3 admitiu, vindo da
  capability/receipt do C3 — se ainda não exposto, uma pequena interface derivada, nunca um novo
  `fpathconf`), envelope e prazo do Git local.
- Saída: capability com memfd selado de `S_G` + `(algoritmo, C)` + `container_digest` + recibo do
  snapshot (rastreabilidade); função pura de parse/validação do consumidor sobre um
  descritor recebido.
- Consumidor previsto: o futuro launcher de E; nenhum caller de produção nesta slice.
- Write-set esperado: (1) o **produtor** físico com separação de privilégio (variante só-física da
  aquisição por descritor da G1C: sem inflate, sem `verify-pack`, sem metadata da fonte; staging →
  finalize → `rename`); **onde** ele roda e sob qual identidade é decisão de U3/#331, não de S1;
  (2) um módulo novo em `app/agent_review/` com o **leitor** que roda como o runner: precondição de
  imutabilidade, Git local só sobre o snapshot publicado (sessão própria sob `RLIMIT_AS`, prazo por
  objeto, cabeçalho estrito, subreaper + teardown da unidade), mapa endereçado por conteúdo e raiz
  derivada de `C`,
  construção do container, selo e **revalidação pós-selo**, validação do consumidor; no C3, uma
  interface derivada que interpreta bytes de tree **fornecidos** (sem processo `git` nem spool
  próprios), consumida em vez de cópias; **sem** `BoundedBlobCarrierV2` no caminho; testes em
  `tests/agent_review/` portando EXP-N1/STRUCT/CAPTURE/RES e o corpus CM-333 aplicável.
- Aceite: todos os casos das famílias N1/STRUCT/CAPTURE/RES/**ARCH-C** com os mesmos contramodelos e
  mutantes (C1–C11: mutações negadas pelo kernel, metadata da fonte ausente, injeções pós-publicação,
  `OfflineClosureComplete`, loose-bomba sem inflate, raiz derivada com ablação, neto `setsid` com
  ablação, crash antes do commit point, leitor como o runner com contra-controle; forja de
  pack/`.idx` sem falso positivo; prazo com ablação; sha256 pelo snapshot; censo de órfãos),
  inclusive contenção medida **no filho** com pai lento, os 9 cabeçalhos hostis, "autenticar A e
  entregar B" e o domínio do limite de componente; paridade com C3 sob o mesmo limite, no corpus
  sintético e no corpus real; orçamentos e envelope adjudicados aplicados antes da expansão; FDs e
  processos lineares em toda falha; nenhum campo de wire novo sem decisão.
- Falsificadores: objeto adulterado aceito; nó de contract A perdido; S parcial retornado;
  escrita pós-compromisso; leak de FD/processo.
- Teto: sem launcher, sem loader, sem S_D, sem receipt, sem G5; **novo grant necessário**.
- **Precondição declarada (U3):** a propriedade de S1 é de componente — "dado um processo produtor
  íntegro, S_G é autêntico e estável". Como a cópia implantada de `app/agent_review/` hoje é o
  checkout gravável pelo UID do runner, nenhum consumidor pode tratar a saída de S1 como proteção de
  #301 até U3 ser resolvida. S1 não deve ser apresentado como "#301 protegido".
- **Decisões de adjudicação necessárias antes do grant de S1** (não são fatos desconhecidos):
  (o) o owner da G1C aceita expor uma variante só-física (sem inflate) **e uma sonda de alternate
  limitada pelo orçamento de entradas** (a de produção enumera sem limite: K3); (p) U3/#331 decidem o
  serviço, a identidade e o provisionamento do produtor, e quem limpa o lixo de staging; (i) valores
  de `snapshot_budget` e `closure_budget`, do prazo por objeto, do prazo total e do envelope do Git
  local, e se a memória agregada da unidade exige cgroup (#320), inclusive a coerência admissão física ↔
  envelope (R4-3) (§8); (ii) aceite do owner de C3 para expor a interpretação de tree sobre bytes
  fornecidos, com o teto de entradas **restante** e uma razão distinta para estouro de entradas
  (interface derivada; não muda o comportamento dos consumidores atuais do C3); (iii) o launcher
  (U3) estabelece o `AuthorizedReaderExecutionContext` (principal, user namespace, mount namespace,
  não-escalada no `exec`) e cria o processo leitor nele; S_G não prova quem criou esse contexto.

### Obrigações sucessoras de S1 (ratificação de S0-C; nenhuma implementada)

S0 ratificado significa `ContractReady`, **não** `ProductionReady`. Os contramodelos abaixo são **falsificadores obrigatórios** da ativação de S1. Uma ativação que não os derrote não pode tornar S_G confiável nem consumível.

```yaml
activation_countermodels:
  TF1:
    countermodel: {readonly_mount_view: {accepted_by_old_detector: true, backing_inode_writable_through_other_mount: true,
                                         git_helper_execution_reproduced: true}}
    lesson: "ReadOnlyView != NoWritableAlias"
    future_owner: S1/U3 activation binding
    required_future_property: "snapshot identity and mount namespace come from the authorized launcher/host context,
                               not only from pathname-level writability tests"
  TF2:
    countermodel: {numeric_runner_ids_visible_inside_other_userns: true, accepted_by_old_detector: true,
                   helper_execution_reproduced: true}
    lesson: "NumericCredentialValues != HostPrincipalIdentity"
    future_owner: S1/U3 activation binding
    required_future_property: "user namespace identity is bound by the authorized launcher/host"
    note: "not 'user namespaces forbidden forever'; S1 may choose to restrict itself to the initial user namespace"
S1_obligations:
  S1_CTX_01:
    name: EXEC_PRIVILEGE_NON_ESCALATION
    proposition: "local Git and every helper executed by the reader cannot acquire credentials or capabilities beyond
                  the AuthorizedReaderExecutionContext"
    minimum_candidate_control: PR_SET_NO_NEW_PRIVS before exec
    countermodels: [setuid executable, setgid executable, file capabilities, X1]
    lifecycle_effect: PrivilegeGainCanBreakKillAuthority      # killpg -> EPERM can defeat teardown
    owner: S1 / U3
    status: NOT_IMPLEMENTED
  S1_RES_01:
    name: ALTERNATE_RESOLUTION_BUDGET
    proposition: "all attacker-controlled work incurred while reading and resolving alternates is charged before
                  expansion beyond the admitted physical acquisition budget"
    resource_dimensions: [manifest_bytes, manifest_lines, pointer_count, descriptor_opens, alternate_depth]
    countermodels: [X2, TF4]
    note: "K3 bounded the directory probe listing only"
    status: NOT_IMPLEMENTED
  S1_SEM_01:
    name: C3_REFUSAL_CAUSE_PRESERVATION
    proposition: "a refusal issued by the C3 structural authority is not converted into budget_nodes when the
                  authoritative cause is known"
    invariant: "RefusalDecisionAuthority = RefusalCauseAuthority (when the cause is known)"
    countermodels: [dotdot, malformed_tree, truncated_tree, duplicate_name, actual_budget_exhaustion]
    evidence: TF3
    status: NOT_IMPLEMENTED
  S1_LIFE_01:
    name: TRANSPORT_OWNERSHIP_FROM_SPAWN
    proposition: "from the first instant a local Git process exists, a lifecycle owner is responsible for teardown on
                  every exit path; no spawn -> unowned -> try/finally window"
    countermodel: exception_between_spawn_and_try              # TF5
    status: NOT_IMPLEMENTED
  S1_PUBLISH_01:
    name: DURABLE_SNAPSHOT_PUBLICATION
    open_requirements: [object_file_fsync, producer_config_fsync, receipt_fsync, relevant_directory_fsync,
                        parent_fsync_before_or_after_atomic_rename_as_required]
    evidence: TF6
    status: NOT_IMPLEMENTED
  R4_3:
    established: true
    affects_authenticity: false
    affects_availability: true
    S0_disposition: ACCEPTED_LIMITATION
    successor_owner: [S1, "#320"]
    required: "harmonize SnapshotAdmissionBudget <-> LocalGitExecutionEnvelope (no new limits chosen in S0)"
```

Antes da primeira implementação, o plano de S1 (grant separado, "#301-S1 IMPLEMENTATION PLAN") precisa reconciliar:
- `AuthorizedReaderExecutionContext` + `no_new_privs`;
- implantação e proveniência do produtor (U3);
- política de storage do host (#331);
- proveniência de `C` na ativação (#319);
- orçamento completo de alternates;
- política de recursos do Git local (#320, R4-3);
- ciclo de vida desde o spawn;
- publicação durável e GC do staging;
- local de produção e handoff da capability.

Cortes seguintes derivados desta arquitetura (não é sequência universal): **slice S_D** — leitor
único com cobrança (spike), identidade/tags/ELF e completude do loader, com R2-1/2/3/7/8 e o corpus
de `exp_deps.py` como contramodelos obrigatórios (depende de U2); launcher/E (depende de U1, U3; U4 decide se dados entram); receipt de execução
(depois de E). Nenhum deles fecha #301 sozinho; G5 (#350) e a composição operacional ficam com seus
owners. Outros owners inalterados: #319 (anchor), #331 (storage do host), #320 (disponibilidade/recurso do Git
local), #354 (transferência de descritor), #298, #314, #350.
