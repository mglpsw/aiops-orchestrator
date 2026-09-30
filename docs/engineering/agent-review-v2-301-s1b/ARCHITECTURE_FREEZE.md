# #301-S1-B: Architecture Freeze, "safe reader context"

```yaml
status: ARCHITECTURE_FREEZE_CORRECTION_REQUIRED   # FINAL_BOUNDARY_ADJUDICATION (#301 5921263805) sobre a emenda final (#301 5921013078; STOP_STRUCTURAL_REDESIGN de 0053d77 adjudicado: redesign não exigido); 3b NOT_CONVERGED; a ratificação 5917390110 é HISTORICAL_SUPERSEDED
ArchitectureFreezeReady: false         # nova adjudicação humana exigida após a revisão independente do successor
ImplementationGrant: false             # ArchitectureFreezeReady != ImplementationGrant
implementation: NOT_STARTED            # B1, B2, B3 não iniciadas
owner_issue: "#301"                    # Refs #301; esta PR não fecha nenhuma issue
```

---

## 1. Live baseline

```yaml
repository: mglpsw/aiops-orchestrator
base:
  master_sha: 2941b558ffb849973df84cef1a4535a015ae559e     # realinhado na emenda final (antes: d3f5946; ab92e89)
  master_tree: ba9e06b38365c954033fdee7ccf41b3fb8239612
  drift_reconciled:
    - "ab92e89 → b3657d3 = #365 (freeze V1, docs-only, só campaign/**; sem app/**, sem caminhos S1-B, sem overlap)"
    - "b3657d3 → d3f5946 = #366 (fix V1-C1/#232: app/agent_review/semantic_chunker.py + testes V1; sem overlap com esta branch; fora do fecho de imports da evidência S1-B: physical_snapshot_v2 → bounded_git_v2, trusted_object_authority_v2)"
    - "d3f5946 → 2941b55 = #351 (distribuição standalone: scripts/, docs/, tests/, manifest; sem app/**; sem overlap; fora do fecho de imports da evidência S1-B)"
    - "rebase linear, sem merge commit"
predecessor:
  slice: S1-A
  state: INTEGRATED
  pr: 361
  source_head: 1763aeb59c468b790744f268b6b7c9caed8ef529
  freeze: docs/engineering/agent-review-v2-301-s1a/ARCHITECTURE_FREEZE.md
  adjudication: docs/engineering/agent-review-v2-301-s1a/IMPLEMENTATION_ADJUDICATION.md (§§1–17)
forge_records:
  "#301 checkpoint S1-A": 5904510377
  "#301 pacote de adjudicação P1": 5905757667
  "#301 adjudicação do mantenedor D-B": 5905959720
  "#350 aceitação de responsabilidade U3": 5905962787
  "#301 adjudicação do mantenedor, rodada 2 (D-B-CAP-TCB, D-B-LIFE-PIDNS, D-B-SAFE-DIR-VALUE)": 5915365542
  "#350 refinamento U3 por D-B-LIFE-PIDNS": 5915369380
  "#301 ratificação ArchitectureFreezeReady (HISTORICAL_SUPERSEDED)": 5917390110
  "#301 correction round 3 (review post-Ready 5370887198)": 5918385379
  "#301 correction round 3b grant (R3 NOT_CONVERGED; N3 → S1-B)": 5919204387
  "#301 final freeze amendment grant (AB-1, AB-2; redesign não exigido)": 5921013078
  "#301 final boundary adjudication (TCB NPTL; FINALIZATION_BARRIER)": 5921263805
  "#46 reconciliada (2026-09-30)": "S1-A INTEGRATED; S1-B próxima, só planejamento; C4 incompleto; G5 não atingido"
state:
  S1_A: INTEGRATED
  S1_B: {architecture: FINAL_BOUNDARY_ADJUDICATION, implementation: NOT_STARTED}
  S1_C: NOT_STARTED
  S1_D: NOT_STARTED
  S_D: NOT_STARTED
  E: NOT_STARTED
  C4: INCOMPLETE
  G5: NOT_REACHED
toolchain_observed_during_P2:          # observação, não requisito; os requisitos estão em §24
  python: CPython 3.11.16
  git: 2.43.0
  kernel: 6.18.33.2-microsoft-standard-WSL2 x86_64
  proc_children_interface: absent       # CONFIG_PROC_CHILDREN desligado; a atribuição usa ppid em /proc/<pid>/stat (§14)
  unprivileged_userns: available         # habilitou a testemunha real de reuso de PID (TF5-G)
```

Qualquer mudança de base torna stale a evidência deste freeze (`Exact-subject evidence`).

## 2. Source and owners

**Autoridade por domínio.** Não existe ordem linear global. Um conflito entre domínios vai ao dono competente e não se resolve por ranking.

```yaml
authority_by_domain:
  roadmap_scope_priority_go_no_go: {owner: "#46"}
  S1_component_semantics: {owners: ["#301", docs/engineering/agent-review-v2-301-s0/CONTRACT.md, "freezes S1 ratificados"]}
  implemented_fact: {owner: "fonte no master exato"}
  stop_redesign: {owner: docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md}
  engineering_method: {owner: AOCM-MPACK (.aocm/)}
  CI_workflow_security: {owners: [.github/AGENTS.md, .github/workflows/]}
```

**Fontes normativas usadas** (âncoras em `ab92e89`):

| Fonte | Conteúdo relevante |
|---|---|
| CONTRACT L36–39 | decisão a: `KEEP_GIT_BUT_CONTAIN_THE_CHILD`, pack reader próprio rejeitado, `RLIMIT_AS` |
| CONTRACT L117–151 | `AuthorizedReaderExecutionContext`, estabelecido **externamente**; `ReaderSelfReport != ReaderContextAuthority` |
| CONTRACT L581–594 | `cat-file --batch` como transporte não confiável e contido; header estrito; `RLIMIT_AS` antes do `exec` |
| CONTRACT L832–846 | 301S-LIFE: `subreaper_required`, `unit_teardown_incomplete` com precedência |
| CONTRACT L946–952 | R4-3 |
| CONTRACT L1158–1176 | TF1, TF2, X1, TF5, R4-3 |
| CONTRACT L1220–1266 | registro de 5 claims: `S1_CTX_01`, `S1_LIFE_01` |
| CONTRACT L1287–1318 | família de recursos; `transport_header_body_prefetch` (Codex 4117693716) |
| #301 5860375191 | plano S1 |
| #301 5861631976 | adendo de recursos: framing/admission de S1-B, `failed_case_exit_nonzero` |
| #301 5880159158 | ruling: `S1_CTX_01` pertence a S1-B |
| Freeze S1-A §19, §22 | consumo: `fchdir(committed_dir_fd)` + `GIT_DIR=.`; escopo de S1-B |
| Adjudicação S1-A §5 | admissão por `type(x) is T`; a admissão do snapshot é fronteira da S1-B |

**Donos**, adjudicados em 5905959720:

| Dono | Possui |
|---|---|
| **S1-B** | mecanismos de verificação (este freeze) |
| **S1-C** | semântica de closure: walk, contabilidade por ocorrência, path bytes, nós, paridade C3, re-hash da closure, seal e revalidação de `S_G` |
| **S1-D** | harness de qualificação dois-UIDs, harness de composição, `S1_COMPLETE_AS_COMPONENT`, handoff tipado para E |
| **#350 (U3)** | composição operacional do launcher; estabelecimento do `AuthorizedReaderExecutionContext`; transição uid/gid; proveniência da expectativa userns/mntns; proveniência do handoff de fd; proveniência do toolchain Git selecionado pelo host; **por D-B-LIFE-PIDNS (5915369380):** criação do PID namespace privado, reader como seu init, mount namespace privado, procfs montado para esse PID namespace, proveniência da expectativa `pidns_identity` |
| **#319** | proveniência de `ExpectedCommit` |
| **#320** | valores de produção de deadline e envelope; memória agregada/cgroup; NPROC geral; NFS/FUSE; política numérica do R4-3 |
| **#331** | proveniência da autorização de storage (C2_B) |
| **#354** | sites legados de transferência de descriptor; a S1-B aplica a lei ao código novo |
| **#363** | folga de timeout do CI (independente) |

## 3. Scope and non-claims

**Escopo.** Mecanismos para que um reader **já estabelecido por autoridade externa** (#350) consuma um snapshot publicado pela S1-A:

```text
PublishedSnapshotV2 → atomic reader handoff → ReaderSnapshotInputV2 → explicit ReaderContextExpectationV2
→ reader-side context verification → immutability + inherited-fd capability-closure admission
→ contained Git unit → strict object transport → per-object authentication → AuthenticatedGitObjectV2
```

**Fora de escopo.** Estabelecer o contexto (#350); closure e `S_G` (S1-C); composição e harness dois-UIDs (S1-D); proveniência de `ExpectedCommit` (#319); C2_B (#331); valores numéricos (#320); pack reader próprio (rejeitado); qualquer implementação (B1, B2, B3 exigem grants próprios).

**Não-claims.** O freeze nega explicitamente:

- proveniência do `AuthorizedReaderExecutionContext`;
- proveniência de `ExpectedCommit`;
- C2_B completo;
- commit autenticado;
- root tree autenticada;
- closure autenticada;
- `S_G` autenticado;
- `S1_COMPLETE_AS_COMPONENT`;
- C4 completo;
- G5;
- resistência universal a DoS;
- limite de memória agregada;
- proveniência do toolchain do host;
- resistência a comprometimento do host;
- que o Git não toca nenhum outro arquivo do host;
- production readiness;
- `PROVED`.

**Leis:**

```text
ReaderContextObservation != ReaderContextAuthority
ObservedContextMatchesExpectation != ExpectationIsAuthorized
ModeSaysReadOnly != NoWritableCapability
PostExecFdObservation != InheritanceProof
CheckThenDup != AtomicCapabilityHandoff
TransportAuthentication != ClosureInterpretation
AbsoluteGitPath != GitBinaryProvenance
BoundedLeak != NoLeak
FailurePrecedence != FailureInformationLoss
ExperimentalEvidence != Implementation != Qualification
ArchitectureFreezeReady != ImplementationGrant
ReaderFdCensus + CPythonSubprocessInheritancePremise → InheritedFdClosureWithinDeclaredTCBDomain   (mecanismo A: histórico, não load-bearing em V1)
ChildBootstrapPreExecCensus + CLOEXEC → InheritedFdClosure   (mecanismo B selecionado, D-B-SPAWN-MECHANISM-R3)
SignalHandlerInstalled != SignalDeliverable
InheritedSignalMask != AuthorizedControlledExitState
SpawnReturned != SpawnOwned   (o dono existe antes de esperar o handshake do exec)
HandlersInstalledBeforeUnblock
PendingSignalAtEntry → delivered only after handler/wakeup machinery exists
ReaderSignalMachinery != ChildBootstrapSignalMachinery
NoChildCanRunInheritedApplicationControlledReaderHandlerBeforeReset   (estreitado na adjudicação de fronteira; handlers internos da NPTL excluídos)
InitialSignalDisposition != AuthorizedReapingState
ForkChildFailure != ReaderControlFlow   (bootstrap do filho: execve OU os._exit, sem terceira saída)
UnitEnvelope = WorkDeadline + TeardownReserve (ambos fixados antes do fork); SpawnHandshakeDeadline <= WorkDeadline; NoUnboundedBlockingOperation dentro do UnitEnvelope
EveryOwnedChildPath → TEARDOWN before final outcome
ArchitectureMechanismSpecified != ImplementationQualified          (lei de convergência, round 3c)
ExperimentSupportsMechanism != ImplementationQualification
ChildExecSignalStateIsConstructedNotInherited
PythonSignalCache != KernelSignalDisposition
InheritedSignalMask != AuthorizedExecSignalMask
ReaderSignalState != ChildExecSignalState
OneStuckChildCannotStarveSiblingTeardown
ForkFailurePreservesCancellationState
OwnerCapabilitiesClosedOnEveryTeardownPath
LibcAddressableSignals != AllKernelSignalNumbers                    (emenda final, AB-1)
NPTLReservedSignal != ApplicationSignalCapability
CannotSafelyCanonicalizeReservedSignal → VerifyCompatibleStateOrRefuse
SameProcessNativeTCBCompromise != ProtectedAdversary                (adjudicação de fronteira)
DeliveredBeforeFinalizationBarrier → belongs_to_current_unit
ArrivesAfterFinalizationBarrier → outside_current_unit
FinalOutcomeIsDerivedAfterTeardown                                  (emenda final, AB-2)
QualificationGap != ArchitectureBlocker   (dado: mecanismo especificado, dono e autoridade existentes, sem contradição normativa, obrigação explícita)
ExperimentalEvidence != ImplementationQualification
PythonException != SIGTERMDefaultAction != SIGKILL != ParentProcessDeath != HostCrash
ProcessGroupKill != WholeUnitTeardown
DominantOutcome != OnlyRecordedFailure
ArchitectureMechanismSpecified != ImplementationBranchQualified
ObservedPidNamespace != AuthorizedPidNamespace
PidNamespaceIdentity != ProcfsViewBinding
PDEATHSIG(parent) != GrandchildLifetimeClosure
DeathOfPIDNamespaceInit → kernel terminates namespace members
MaintainerAdjudication != ImplementationGrant
```

## 4. Applicability

```text
Given   AuthorizedReaderExecutionContext established by #350 (U3)
        ∧ ReaderContextExpectationV2 supplied explicitly by the host
        ∧ CompletePublicationV2.snapshot : PublishedSnapshotV2 (S1-A)
        ∧ reader process dedicated (single-thread, no other children) and created by #350
        ∧ reader is init (PID 1) of a private PID namespace established by #350, with that namespace's procfs
          and a private mount namespace (external-termination closure, §14; D-B-LIFE-PIDNS ADOPTED, 5915365542)
        ∧ domain(§24)
S1-B ⊢  descriptor-bound, privilege-non-escalating, contained Git transport whose delivered
        objects are content-address-authenticated, with lifecycle owned from spawn,
        or a typed refusal.
```

- **Domínio V1:** Linux com pidfd completo (`pidfd_open`, `pidfd_send_signal`, `waitid(P_PIDFD)`), x86_64/aarch64 (herdado da S1-A), CPython 3.11, filesystem no domínio da S1-A, Git ≥ piso por recurso (§24).
- **userns: `initial_user_namespace_only`**, com disposição `AUTHORIZED_CONTRACTION` (D-B-USERNS). Não é lei universal de segurança; ampliar exige scope reopen, desenho explícito e nova qualificação.
- **PID namespace privado + mount namespace privado + procfs desse PID namespace** (D-B-LIFE-PIDNS, **ADOPT**, [5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)):
  - O #350 estabelece os três **antes da queda final de privilégios** do reader, no userns inicial; o launcher é privilegiado, o reader não.
  - A S1-B **não** cria nem monta nada disso; ela só **verifica** (B-RC-06).
  - Ausente ou divergente → recusa tipada `reader_pid_namespace_required`.
  - É uma extensão explícita do `AuthorizedReaderExecutionContext` **V1**, não uma lei universal sobre reader contexts futuros.
- **Fora do domínio → recusa tipada**, nunca operação degradada. Exemplos: kernel sem pidfd, userns não inicial, Git abaixo do piso.

## 5. Obligation domains

Exatamente 11 domínios. A matriz completa está em §16.

| Domínio | IDs | Propriedade central | Classe | Dono |
|---|---|---|---|---|
| HANDOFF | B-HO-01..04 | a capability da S1-A cruza a fronteira sem perda nem rebinding | MIXED | S1-B + API aditiva na S1-A |
| READER_CONTEXT | B-RC-01..06 | observação == expectativa explícita (inclui `pidns_identity`); procfs competente e ligado ao pidns do reader; userns inicial | LIVE | S1-B verifica; #350 estabelece |
| IMMUTABILITY | B-IMM-01..04 | `¬PrincipalCanMutate(reader, snapshot)` por inode | LIVE | S1-B |
| CAPABILITY_CLOSURE | B-CAP-01..05 | reader e Git recebem só fds de allowlist **tipada** (inclui o canal de wakeup de sinais); lado do reader observado; herança no filho provada pelo census pré-exec do bootstrap do filho (mecanismo B) | LIVE | S1-B verifica; #350 fecha na origem |
| PRIVILEGE | B-PRV-01..04 | caps = 0; `PR_SET_NO_NEW_PRIVS` antes do exec (`S1_CTX_01`) | LIVE | S1-B |
| EXEC_CONFINEMENT | B-EXE-01..05 | Git relativo ao fd; env e args autorados; request só com OID completo | MIXED | S1-B |
| TRANSPORT | B-TRN-01..05 | header estrito em bytes; zero prefetch; admissão como type-state; framing exato; mesmos bytes | MIXED | S1-B |
| OBJECT_AUTH | B-AUTH-01..03 | preimage canônica Git == OID pedido | MIXED | S1-B; posição semântica é da S1-C |
| RESOURCE | B-RES-01..06 | deadlines; `RLIMIT_AS` pré-exec; loop único; stderr sem conteúdo; R4-3 Policy B | MIXED | S1-B mecanismo; valores da #320 |
| LIFECYCLE | B-LIF-01..12 | dono antes do filho; identidade pelo kernel; teardown em toda saída **controlada** e término externo fechado pelo init do pidns (D-B-LIFE-PIDNS); zero sobreviventes ou falha tipada (`S1_LIFE_01`) | LIVE/MIXED | S1-B |
| OUTCOME/QUALIFICATION | B-OUT-01..03, B-QUA-01..04 | reason codes fechados; precedência sem perda de informação; harness honesto; census | STATIC/MIXED | S1-B |

## 6. Snapshot handoff

**B-HO-01.** Elegível só se `type(x) is PublishedSnapshotV2 ∧ minted_by_S1A_factory(x) ∧ not_closed(x)`. Recusados: residual, subclasse, `__class__` falsificado e instância criada por `object.__new__`.

**B-HO-02. Duplicação atômica** (D-B-HANDOFF: `atomic_admit_dup_install_under_S1A_lock`):

```text
exact-type + genuine + open admission
→ with the source descriptor's ownership protected (lock of the S1-A _SharedFdSlotV2)
→ F_DUPFD_CLOEXEC
→ immediate install into a pre-existing linear owner
→ return owned duplicate + existing PublishedSnapshotBindingV2
```

- **Proibido:** `check registry → release lock → dup(integer fd)`. Motivo: `committed_dir_fd` (L1768) devolve o número sob o lock e solta o lock, e o número pode ser reutilizado antes do `dup` (`CheckThenDup != AtomicCapabilityHandoff`).
- **Implementação futura:** exige uma API pública **aditiva** mínima em `app/agent_review/physical_snapshot_v2.py`. Essa mudança **reabre a qualificação focal da S1-A**: a suíte, os sweeps `MemoryError`/`KeyboardInterrupt` e o census N1/N2 em CPython 3.11. O census congelado não é editado. **Este freeze não implementa a API.**

**B-HO-03a. Binding de kernel.** `statx(owned_dup)` revalida **só** a projeção observável pelo kernel: `mount_id`, `st_dev`, `st_ino`, `st_uid`, `st_gid`.

**B-HO-03b. Metadata carregada.** `snapshot_id` e `committed_parent_identity` vêm no binding. O `statx` do fd **não** os re-deriva, e fingir o contrário é overclaim.

**B-HO-04. Locator** (D-B-LOCATOR): derivado de `PublishedSnapshotBindingV2`. Um tipo de locator paralelo é **proibido**.

`ReaderSnapshotInputV2` é o valor do lado do reader: `(fd herdado, binding esperado)`, admitido por B-HO-03a antes de qualquer uso. O handoff de processo e de uid pertence à #350.

## 7. Reader context

- **B-RC-01.** `ReaderContextExpectationV2` é **explícita e fornecida pelo host** (#350). Não existe `from_current_process()` nem default derivado de `/proc`, porque isso seria tautologia. **Proposição congelada** (os nomes não são API congelada enquanto não houver tipo de implementação):

  ```yaml
  ReaderContextExpectationV2:
    principal: {ruid, euid, suid, fsuid, rgid, egid, sgid, fsgid}
    supplementary_groups: [...]
    userns_identity: <kernel ns identity>      # V1: initial user namespace (D-B-USERNS)
    mntns_identity: <kernel ns identity>       # private mount namespace established by #350
    pidns_identity: <kernel ns identity>       # private PID namespace established by #350 (D-B-LIFE-PIDNS)
    capability_expectation: {CapEff: 0, CapPrm: 0, CapInh: 0, CapAmb: 0}
    no_new_privs: expected state
    snapshot_expectation: derived from PublishedSnapshotBindingV2 (B-HO-04)
  ```

- **B-RC-02. Procfs competente.** O truth-maker:
  - abrir um descriptor de procfs;
  - verificar `fstatfs(fd).f_type == PROC_SUPER_MAGIC`;
  - fazer as observações relativas a essa vista admitida.

  - verificar que a vista pertence ao PID namespace do reader: `readlink(<procfs>/self) == str(getpid())`; no domínio V1, `getpid() == 1` e a vista do procfs observa o reader como PID 1 (**ProcfsViewBinding**, B-RC-06).

  Onde houver syscall mais competente que parsing textual, ela vence: `getresuid`, `getresgid`, `getgroups`, `prctl(PR_GET_NO_NEW_PRIVS)`, `capget`. Não conseguir observar ou observar ambiguidade (por exemplo chave duplicada) → recusa.
- **B-RC-03. Comparação completa, antes de qualquer spawn:**
  - ruid, euid, suid e fsuid;
  - rgid, egid, sgid e fsgid;
  - grupos suplementares;
  - identidade do userns, do mntns **e do pidns** (cada uma com a respectiva expectativa, nunca uma pela outra: `mntns identity != pidns identity`);
  - conjuntos de capabilities;
  - `NoNewPrivs`.
- **B-RC-04. userns V1:** `initial_user_namespace_only`. O inode de `ns/user` é igual à constante do kernel para o userns inicial e à expectativa. O mntns é comparado à expectativa, porque não há constante de kernel equivalente.
- **B-RC-05.** `ObservedContextMatchesExpectation != ExpectationIsAuthorized`. A proveniência da expectativa é da #350.
- **B-RC-06. Identidade do PID namespace e vínculo do procfs** (D-B-LIFE-PIDNS). São **dois controles distintos** (`PidNamespaceIdentity != ProcfsViewBinding`):
  - **(a) Identidade:** abrir `ns/pid` do próprio reader pela vista de procfs admitida → `fstat`/`statx` → identidade de namespace do kernel (dev, ino do nsfs), comparada com `pidns_identity` **fornecida pelo host**. A expectativa **nunca** é derivada do reader (`ObservedPidNamespace != AuthorizedPidNamespace`).
  - **(b) Vínculo do procfs:** `f_type == PROC_SUPER_MAGIC` e a vista observa o reader como PID 1 (`readlink(<procfs>/self) == "1"`), com `getpid() == 1`.
  - **`getpid() == 1` não substitui (a).** Um init de *outro* pidns também vê PID 1, e só a comparação com a identidade fornecida distingue qual namespace é.
  - O mntns continua comparado à `mntns_identity` fornecida pelo host (B-RC-03).
  - Qualquer divergência → `reader_pid_namespace_required`.

## 8. Immutability + inherited FD capability closure

**IMMUTABILITY** (`ModeSaysReadOnly != NoWritableCapability`):

- **B-IMM-01.** O reader não é dono dos inodes materiais. A verificação é um walk por fd com `O_NOFOLLOW` a partir do fd admitido, limitado e cruzado com `receipt.files_copied`.
- **B-IMM-02.** O reader não tem autoridade de escrita pelos bits de modo aplicáveis (owner, grupo se o gid estiver em seus grupos, other) nem por ACL. A presença de xattr de ACL POSIX → recusa conservadora (over-rejection declarada). Onde houver verdade derivada do kernel, preferi-la.
- **B-IMM-03.** Só arquivo regular e diretório. Special files e symlinks → recusa.
- **B-IMM-04.** `ReadOnlyView != NoWritableAlias`. TF1 (alias de mount) é falsificador obrigatório. O truth-maker é a identidade por inode, nunca `access(path)`.

**CAPABILITY_CLOSURE:**

- **B-CAP-01.** O reader começa com um conjunto fechado de fds, **observado diretamente** (census tipado do reader).
- **B-CAP-02.** Nenhum fd herdado dá escrita ou outra autoridade sobre o snapshot, o storage, a policy do host ou qualquer outro recurso que carregue autoridade.
- **B-CAP-03.** O Git herda só `{0, 1, 2}`: o request, a saída e o stderr do protocolo.
- **B-CAP-04. Allowlist tipada.** Verificada com `fstat`, `fcntl(F_GETFL)` (modo de acesso e `O_PATH`) e `fcntl(F_GETFD)` (`CLOEXEC`):

  ```yaml
  AllowedFd: {role, kernel_identity, object_type, access_mode, cloexec, inherited_by_reader, inherited_by_git}
  ```

- **B-CAP-05. Onde a herança é estabelecida** (correction round 3; D-B-SPAWN-MECHANISM-R3: **mecanismo B, fork/exec explícito**):
  - O conjunto de fds **do reader** é observado diretamente: census tipado antes do spawn, com os papéis de wakeup (abaixo).
  - **No filho, o bootstrap explícito** (entre `fork` e `execve`) faz: `dup2` do stdio do protocolo; `RLIMIT_AS`; fecha todo fd não permitido; **census pré-exec exato e estável** (lista, `fstat` de cada um, descarta o fd transitório da enumeração, retém todos os outros); exige `{0,1,2}` + o canal de erro de setup (CLOEXEC); então `execve`. Divergência → relato pelo canal de erro → recusa tipada.
  - **Herança admitida** = conjunto exato pré-exec + semântica CLOEXEC do kernel. Isto é prova **direta** no caminho primário V1 (`ChildBootstrapPreExecCensus + CLOEXEC`), e não mais `CPythonSubprocessInheritancePremise`.
  - `/proc/<git-pid>/fd` pós-exec é corroboração (`PostExecFdObservation != InheritanceProof`). P2 (B): `[0,1,2]`; ablação `B_no_close` + census desligado → fd herdado.
  - **D-B-CAP-TCB** (AUTHORIZED_CONTRACTION, 5915365542) continua **válida como registro da opção A**, mas **deixa de ser load-bearing** no caminho primário V1.
  - **Evidência A (histórica, calibrada, correction round 3):** P2c REV01 com census estável mostra que o fd conhecido do reader é visível em `preexec` (antes de `close_fds`), que após o exec o conjunto é exatamente `[0,1,2]`, e que o mutante com `pass_fds=(extra,)` torna o witness RED (`[0,1,2,3]`).
  - **Achado P2:** `subprocess.DEVNULL` abre `/dev/null` com **O_RDWR**. Um papel somente leitura exige um fd `O_RDONLY` explícito.

| fd (reader) | role | object_type | access | cloexec | inherited_by_git |
|---|---|---|---|---|---|
| 0 | stdin do reader (launcher) | chr/fifo | r | — | não (substituído) |
| 1, 2 | canais de resultado e diagnóstico | fifo | w | — | não (substituídos) |
| snapshot (owned dup) | snapshot_dir | dir | r (nunca `O_PATH`) | **sim** | não (o Git usa o cwd do `fchdir`) |
| procfs | procfs_view | dir | r | sim | não |
| pipes do protocolo | request / response / stderr | fifo | w / r / r | sim (lado do reader) | só a outra ponta, como 0/1/2 |
| `signal_wakeup_read` | wakeup de sinais (B-LIF-09) | fifo (pipe) | r, **não bloqueante** | **sim** | **não** |
| `signal_wakeup_write` | wakeup de sinais (B-LIF-09), registrado em `set_wakeup_fd` | fifo (pipe) | w, **não bloqueante** | **sim** | **não** |
| canal de erro de setup do filho | relato de falha pré-exec (mecanismo B) | fifo (pipe) | r (reader) / w (filho) | **sim** | não (fecha no exec por CLOEXEC) |

**Canal de wakeup** (correction round 3, finding 4148411697):
- **Criação:** `pipe2(O_NONBLOCK | O_CLOEXEC)`; em seguida `set_wakeup_fd(write_end)`; o `select` monitora `read_end`; o Git não herda nenhuma das pontas.
- **Encerramento:** `set_wakeup_fd(-1)` **antes** de fechar `write_end`; depois cada ponta é fechada exatamente uma vez.
- **Wakeup fd pré-existente:** no reader dedicado nunca é adotado; `set_wakeup_fd(-1)` na entrada retornando ≠ -1 → recusa `reader_signal_wakeup_preexisting` (fail-closed).
- O canal é **dedicado**: nunca é compartilhado com o protocolo (CM-W6).
- **Posse por fase** (round 3b):
  - **reader:** possui as duas pontas;
  - **filho pré-exec:** não possui nenhuma depois do passo de reset do bootstrap (`set_wakeup_fd(-1)` e só então o fechamento explícito das cópias, §14 item 4);
  - **Git após o exec:** não herda nenhuma.
- **Três barreiras independentes** mantêm as pontas fora do Git: CLOEXEC, o fechamento explícito no reset e o laço de fechamento pré-exec. P2d CM-W3a, CM-W3b e CM-W3d mantêm uma de cada vez e dão `[0,1,2]`; CM-W3c remove as três (e o census) e dá `[0,1,2,4]`.
- **Discriminador de N2:** R3B-S4 e R3B-S4b (§14, B-LIF-12).
- **Canal de erro de setup:** o pai fecha sua cópia da ponta de escrita logo depois do `fork`, antes do `select`. Sem isso, o EOF nunca chega e todo spawn cairia em `unit_deadline`.

## 9. Privilege

- **B-PRV-01.** Antes de qualquer Git ou helper: CapEff, CapPrm, CapInh e CapAmb = 0. CapBnd é registrado.
- **B-PRV-02.** `PR_SET_NO_NEW_PRIVS = 1` no reader dedicado antes do spawn. É monotônico e herdado. Verificado por read-back com `PR_GET_NO_NEW_PRIVS`.
- **B-PRV-03.** O filho observa `NoNewPrivs: 1`. P2 mostra 1 no mecanismo e 0 na ablação.
- **B-PRV-04. Falsificadores:** executável setuid, executável setgid, file capabilities, X1, e ganho de privilégio que quebra a kill authority (`killpg` → EPERM).
  - As testemunhas reais exigem um binário setuid/file-caps e um segundo principal. São qualificação de **B2**; no ambiente sem esses recursos → `gate_unavailable`, que não conta como PASS.

## 10. Contained execution + TCB floor

**B-EXE-01. Execução relativa ao fd.**
- O reader dedicado faz `fchdir(owned_dup)`, e o spawn usa `cwd=None` e `GIT_DIR=.`.
- Nenhum path do snapshot entra em argv ou env.
- **Claim calibrada:** o acesso ao repositório e ao object store está ancorado no snapshot admitido. **Não** se afirma que o Git não toca outro arquivo do host.
- P2: o path renomeado com um decoy plantado não afeta o filho; na ablação `cwd=<path>`, o filho entra no decoy.

**B-EXE-02. Environment por allowlist.** Sem `os.environ`. Base: `PATH` fixo, `LC_ALL=C`, `GIT_DIR=.`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `HOME=/dev/null`, `GIT_TERMINAL_PROMPT=0`.
- Ausentes: `LD_*`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_CONFIG*` além dos acima, SSH e helpers.
- Reusar `bounded_git_environment_v2()` só onde a semântica coincide. **Nenhum import de símbolo privado** (por exemplo `_BOUNDED_GIT_CONFIG_ARGUMENTS_V2`). Se necessário, uma primitive **pública** pequena em `bounded_git_v2`.

**B-EXE-03. Argumentos autorados:**

```text
--no-replace-objects
-c core.hooksPath=/dev/null
-c core.fsmonitor=false
-c protocol.allow=never
-c safe.directory=*
```

- **D-B-SAFE-DIR / D-B-SAFE-DIR-VALUE** (adjudicação do mantenedor, [5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)):

  ```yaml
  D-B-SAFE-DIR:
    maintainer_adjudication:              # D-B-SAFE-DIR-VALUE: ADOPT
      invocation_scope: {form: "-c safe.directory=*", scope: single_authorized_Git_invocation}
      ambient_configuration: {allowed: false}
    reason: >-
      Git's safe.directory ownership heuristic is not the authority for the S1-B reader/snapshot
      trust boundary. S1-B independently establishes what it guards: snapshot kernel identity (B-HO-03a),
      reader non-mutability (B-IMM), producer-authored config (S1-A C5), descriptor-relative binding
      (B-EXE-01), hooksPath=/dev/null, fsmonitor=false, env allowlist.
  ```

  - **Evidência de suporte, não autoridade da decisão.** Fato observado no P2c REV06 (Git 2.43.0, dono estrangeiro simulado com `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`): a heurística recusa na **descoberta** de repositório (`dubious ownership`), mas a invocação da S1-B com `GIT_DIR=.` explícito **não** é submetida a ela: `cat-file` responde mesmo sem `safe.directory`.
  - O comportamento com `GIT_DIR` explícito **não é documentado**, pode variar por versão e **não é contrato universal**. O `-c safe.directory=*` por comando mantém a invocação estável entre versões **sem** que a S1-B dependa da heurística.
  - A alternativa de valor por path foi descartada porque não acrescenta nada à fronteira da S1-B, não porque "path seria ruim".
- **Lazy fetch é estruturalmente irrelevante** (D-B-GIT-FLOOR, caminho preferido), porque:
  - a config do snapshot é autorada pelo produtor S1-A: P2b observou `[core] repositoryformatversion/bare` e, em sha256, `[extensions] objectformat`, **sem** remote, promisor ou partialclone;
  - a S1-A exclui `.promisor` e metadata de remote (C5, A13);
  - argv e env são autorados.

  Logo `GIT_NO_LAZY_FETCH` (Git 2.45) não é necessário e não eleva o piso.

**B-EXE-04. Request.** Só OID completo, hex **lowercase canônico**: `[0-9a-f]{40}` para sha1 ou `[0-9a-f]{64}` para sha256, conforme `receipt.declared_object_format`. Nenhuma expressão de revisão (`HEAD:x`, `C^{tree}`, `refs/...`).

**B-EXE-05. Binário.** Git por caminho absoluto selecionado pelo host (`AbsoluteGitPath != GitBinaryProvenance`).

**TCB floor** (premissas externas, não provadas pela S1-B):

```yaml
S1B_TCB_FLOOR:
  trusted_external_premises:
    - semântica do kernel Linux usada (fd, statx, fstatfs/procfs, prctl, pidfd, waitid, CLOEXEC em exec, reparenting para subreaper)
    - CPython 3.11 + stdlib
    - "bootstrap do filho (mecanismo B, V1): os.fork, signal.set_wakeup_fd(-1), sigaction(2) via libc (ctypes) para escrever e ler disposições no kernel, signal.pthread_sigmask, os.dup2, resource.setrlimit, os.listdir/os.fstat, os.close, os.execve e os._exit do CPython 3.11 sobre as syscalls do kernel; o conjunto de fds pré-exec é INSPECIONADO pelo próprio bootstrap (B-CAP-05); não há premissa de close_fds do _posixsubprocess no caminho primário; o reader é single-thread (§14 item 1), então o fork em Python não herda locks de outras threads"
    - "histórico (mecanismo A, não load-bearing em V1): caminho filho de _posixsubprocess (dup2 → preexec_fn → close_fds → exec)"
    - "kernel: máscara e sinais pendentes são preservados no execve; fork() zera o conjunto pendente do filho; pthread_sigmask normaliza a máscara da thread única do reader (B-LIF-10)"
    - "kernel: SIGCHLD=SIG_IGN sobrevive ao execve; sa_flags (SA_NOCLDWAIT) são zerados no execve; SIG_IGN ou SA_NOCLDWAIT → auto-reap do filho, pidfd_open ESRCH e waitid ECHILD (B-LIF-11; observado no P2d R3B-C1/C2)"
    - "kernel: o init de um PID namespace descarta sinais SIG_DFL vindos de dentro do namespace (observado no P2d R3B-S1-pidns)"
    - "CPython 3.11: signal.signal instala via sigaction sem SA_NOCLDWAIT (observado); a S1-B lê o estado de volta de qualquer forma (B-LIF-11)"
    - "kernel: morte do init de um PID namespace mata todos os processos do namespace (§14)"
    - libc e dynamic loader
    - executável Git selecionado pelo host e suas bibliotecas de runtime
    - semântica de filesystem no domínio admitido
  not_proved_by_S1B:
    - proveniência desses componentes (dona: #350)
    - resistência a comprometimento do host
    - "detecção ou sobrevivência a adulteração arbitrária, no mesmo processo e por syscall crua, das disposições dos sinais reservados à glibc/NPTL (SameProcessNativeTCBCompromise != ProtectedAdversary)"
    - autoria do handler instalado no kernel para os sinais reservados
S1B_V1_TCB:                            # adjudicação de fronteira, #301 5921263805
  kernel: Linux
  libc_threads: glibc/NPTL
  interpreter: CPython_3_11
  child_bootstrap: S1B_explicit_fork_exec_bootstrap
NPTL_RESERVED_SIGNALS:                 # 32, 33 no runtime glibc/NPTL qualificador (ocultos das APIs da aplicação)
  semantic_role: libc_internal
  application_controlled: false
  S1B_canonicalized: false
  S1B_handler_authorship_verified: false
  trusted_as_part_of_TCB: true
  SigCgt_required_clear: false         # a glibc 2.39 instala handler no 33 quando o processo cria qualquer thread (observado)
  raw_rt_sigaction_rewrite: forbidden
  GitNotTrustedAsObjectTruth: true     # todo objeto entregue é re-hashado (§12)
```

## 11. Transport type-state

```text
RequestedObjectV2 → ParsedObjectHeaderV2 → BodyAdmittedObjectV2 → AuthenticatedGitObjectV2
```

**B-TRN-01. Header estrito em bytes, sem `int()`:**

```text
<oid> SP <kind> SP (0|[1-9][0-9]{0,18}) LF        kind ∈ {commit, tree, blob, tag}
```

- `<oid>` é igual ao OID pedido.
- `<oid> SP missing LF` → `transport_object_missing`.
- `ambiguous` ou qualquer outra forma → `transport_header_invalid`.
- Header maior que `MAX_HEADER` → `transport_header_oversize`.

**B-TRN-02. Zero prefetch.** Nenhum byte do body é consumido antes de `BodyAdmittedObjectV2` (`TRANSPORT_HEADER_BODY_PREFETCH`, Codex 4117693716). O freeze fixa a **propriedade** e as **restrições** do domínio de pipes anônimos do protocolo (correction round 1, S1B-REV-03; os pipes do mecanismo B são os mesmos pipes anônimos):
  - **`MSG_PEEK` está excluído.** Pipes anônimos não são sockets: `recv(MSG_PEEK)` → `ENOTSOCK` (P2c REV03).
  - **Mecanismo conforme:** leitor de header limitado e exato em bytes, `os.read(fd, 1)` **por syscall** até o LF, com teto `MAX_HEADER`, direto no fd.
  - **Não conformes** (consomem o body do kernel, P2c REV03: 0 dos 11 bytes restantes): `BufferedReader` (inclusive `Popen.stdout`, do mecanismo A histórico), `readline()`, `read(n)` com `n` maior que o restante possível do header, e qualquer objeto de arquivo com buffer sobre o fd do protocolo.
  - **Discriminador:** consumo **observado no kernel**, com `FIONREAD` no pipe no momento da admissão igual a `size + 1`, e não uma transição de estado.

**B-TRN-03. Admissão como type-state.** Não é callback arbitrário. Só o estado `BodyAdmittedObjectV2` habilita a leitura do body.
- A admissão aplica o envelope próprio e a restrição explícita do chamador.
- Uma recusa mata e reapeia a unidade antes de propagar.
- A causa é preservada.

**B-TRN-04. Framing exato.** Exatamente `size` bytes e um LF, sem overread reaproveitado. Curto → `transport_truncated`; trailer errado → `transport_framing_invalid`.

**B-TRN-05. Mesmos bytes.** `BytesHashed == BytesDelivered`. Nunca `authenticate(read_A)` seguido de `deliver(read_B)`. Os bytes entregues são um objeto imutável.

**Paridade com o Git real (P2b).** Os 13 objetos de cada formato (commit, tree, blob e tag) passaram na gramática, e o OID ausente produziu exatamente `<oid> missing`. Resultado em sha1 e sha256, Git 2.43.0.

## 12. Object authentication

**B-AUTH-01. Preimage canônica:**

```text
H( kind + b" " + decimal_ascii(actual_body_length) + b"\0" + exact_body_bytes ) == requested_oid
```

- `actual_body_length` é o comprimento **real** do body, não o declarado.
- **Oráculo positivo:** `our_digest == git_generated_oid`. **SHA-1 obrigatório.**
- **SHA-256 está no domínio** porque o piso de §24 admite `extensions.objectformat` e a paridade P2b passou. Fora do piso → recusa tipada.
- **Mutantes observados RED no P2b** (0 sobreviventes em 13 objetos × 2 formatos): omitir tipo, omitir SP, omitir NUL, usar o tamanho do header em vez do comprimento real, fazer hash só do body, alterar o tipo.
- **Regra de projeção (correction round 1, S1B-REV-05):**

  ```yaml
  B3_implementation:
    MUST: "reuse an existing semantically suitable canonical helper, OR extract a shared derived primitive under its proper owner"
    MUST_NOT: "introduce another independent private reimplementation of the canonical preimage"
  existing_helpers_at_ab92e89:
    strict_json.git_blob_oid: {constructs_preimage: true, kinds: [blob], algorithms: [sha1], consumer: caem_consumer/f0, owner: app/common (shared)}
    trusted_object_authority_v2._verify_loose_object_hash_v2: {constructs_preimage: false, note: "hashes the inflated stored loose object, which already contains the header"}
  suitability_for_S1B_as_is: none      # (kind, body, sha1|sha256) not covered; registered, not chosen now
  choice: "deferred to the B3 grant, with the owner of the shared module"
  ```

**B-AUTH-02.** O `kind` é um dos tipos Git admitidos e está criptograficamente amarrado ao OID pelo hash.
- A S1-B **não** estabelece a posição semântica na closure commit/tree, que é da S1-C.
- Um `expected_kind` explícito do chamador pode ser comparado como restrição; isso não deriva semântica de closure.

**B-AUTH-03.** `AuthenticatedGitObjectV2` só existe depois de framing válido, body completo, hash completo, OID correto e tipo correto.

Limite: `AuthenticatedGitObjectV2 != AuthenticatedCommit != AuthenticatedClosure != AuthenticatedStableSubject`.

## 13. Resource envelope

- **B-RES-01.** Deadline monotônico por objeto, do request ao LF final, vindo de policy explícita. **Sem default de produção.**
- **B-RES-02. Lei do deadline** (round 3; reforçada na round 3b; reformulada na iteração 2 da 3b, review F3 / Codex 4149311518): `UnitEnvelope = WorkDeadline + TeardownReserve (ambos fixados antes do fork); SpawnHandshakeDeadline <= WorkDeadline; NoUnboundedBlockingOperation dentro do UnitEnvelope`.
  - Antes do `fork`, a unidade fixa dois instantes absolutos: o **fim do trabalho** (deadline de trabalho) e o **fim do envelope** (fim do trabalho + reserva de teardown).
  - O deadline de trabalho cobre o `fork`, a aquisição do pidfd, o setup do filho, o handshake do exec e o transporte.
  - O teardown usa **o que resta até o fim do envelope**, nunca uma janela nova.
  - Qualquer espera bloqueante sem limite dentro desse domínio é contramodelo (§23).
  - **Mecanismo B:** o filho tem dono (pidfd) **logo após o `fork`**. O `select` do HANDSHAKE observa `setup_error_read`, `signal_wakeup_read` e o relógio:
    - EOF → exec **presumido**. O canal CLOEXEC também fecha se o filho morrer antes do exec, e esse caso aparece depois como `transport_failed` (resíduo declarado em §14);
    - dados → falha tipada de setup ou exec;
    - `signal_wakeup_read` legível → `termination_requested`, **nunca** `unit_deadline`, porque preservar a causa faz parte do outcome (N4);
    - deadline → `pidfd_send_signal(SIGKILL)` e a posse passa ao **teardown limitado**: `waitid(P_PIDFD, WEXITED|WNOHANG)` repetido até o fim da reserva, e depois `unit_teardown_incomplete` se o processo ainda existir. **Nunca** um `waitid` bloqueante após o deadline (4148788387), porque a task pode estar em sono não interrompível.
  - **P2d:**
    - stall pré-exec de 3 s com deadline de 1 s: B recupera o controle em **1,001 s**, filho morto e reapeado, 0 sobreviventes (R3B-H1). A (`Popen`) só recupera em **3,002 s**. A ablação "B sem deadline no handshake" leva **3,002 s**.
    - **R3B-H2:** um test double faz o `WNOHANG` responder "ainda vivo" por 8 s, simulando um SIGKILL que não completa. Com trabalho de 1,0 s e reserva de 1,5 s (envelope de 2,5 s), a unidade termina em **2,503 s** com `unit_teardown_incomplete` (primária `unit_deadline`) e 0 esperas bloqueantes; os 3 ms de excesso vêm da granularidade do laço de polling. A ablação com reap bloqueante termina em **8,003 s**. Isso qualifica a lógica do limite, não o comportamento de estado D real.
  - P2 (TF5-D): um filho travado **depois** do exec, antes do protocolo, gera `unit_deadline`.
- **B-RES-03.** `RLIMIT_AS` antes do exec, aplicado pelo **bootstrap do filho** (mecanismo B). P2 (B, correction round 3): o filho observa o limite como primeira ação; a ablação `prlimit` depois do spawn mostra `unlimited`.
- **B-RES-04.** Um único loop `select` cuida do progresso de stdin, stdout e stderr, do **canal de erro do handshake**, dos deadlines e da **ponta de leitura do wakeup de sinais** (`signal_wakeup_read`, B-LIF-09), sem threads.
- **B-RES-05.** Drenar todo o stderr e **não publicar stderr bruto**. O máximo é `stderr_observation: {bytes_seen, truncated, digest: opcional}`, e o reason code não carrega conteúdo (`BoundedLeak != NoLeak`).
- **B-RES-06. R4-3, Policy B** (`ReportedCauseRequiresObservedTruthMaker`). `transport_envelope_exceeded` só quando a causa foi observada. Sem truth-maker competente → `transport_failed`.

A #320 mantém: valores de produção de deadline, memória agregada, cgroup, NPROC geral, NFS/FUSE e política numérica do R4-3. **Este freeze não fecha a #320.**

## 14. Lifecycle

**Propriedade**, não API:

```text
OwnerEstablishedBeforeChildExists ∧ KernelBoundProcessIdentity ∧ TeardownOnEveryExit
∧ ZeroAttributableSurvivorsOrTypedFailure
```

**Domínios de término** (correction round 1, S1B-REV-02). `TeardownOnEveryExit` **não** é afirmado sem esta partição:

| Domínio | Eventos | Mecanismo | Evidência P2c |
|---|---|---|---|
| `controlled_exit_domain` | exceções Python, inclusive `BaseException`; SIGTERM, SIGINT e SIGHUP | **bootstrap de sinais na ordem de B-LIF-10** (handlers e wakeup antes do desbloqueio) + SIGCHLD normalizado (B-LIF-11) + teardown do reader (§14, itens 1–9) + **handlers de sinal** que só registram e acordam o loop (canal de wakeup tipado, §8), **nunca levantam**; o loop único (B-RES-04) transita para `TEARDOWN`; o teardown não é interrompível por handler | T1: SIGTERM sem handler deixa **2 sobreviventes** (reproduzido). T2: com handler, 0 sobreviventes. **Leia a nota de evidência abaixo** |
| `external_termination_domain` | SIGKILL, OOM-kill ou crash do reader (a morte do reader não é interceptável) | **reader é init de um PID namespace privado** estabelecido por #350: na morte do init, o **kernel** mata todos os processos do namespace, inclusive netos com `setsid`. A S1-B **verifica** a pré-condição e recusa sem ela | T3: SIGKILL com handler → 2 sobreviventes. T4: `PR_SET_PDEATHSIG` no filho → o neto com `setsid` sobrevive (PDEATHSIG **rejeitado**, `ProcessGroupKill != WholeUnitTeardown`). **T5: reader init de pidns + SIGKILL → 0 sobreviventes.** T6: init sem handler ignora SIGTERM do ancestral. T7: init com handler → teardown controlado (ver a nota de evidência) |
| `unrecoverable_nonclaim` | crash do host ou do kernel; SIGSTOP do reader | — (com o host, todos os processos desaparecem; parar o reader é DoS, da #320) | — |

- **D-B-LIFE-PIDNS: ADOPT** ([5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)). O PID namespace privado, o mount namespace privado e o procfs desse pidns estendem explicitamente o `AuthorizedReaderExecutionContext` V1.
  - **Estabelecido pelo #350** (refinamento de U3 em [5915369380](https://github.com/mglpsw/aiops-orchestrator/issues/350#issuecomment-5915369380)).
  - **Verificado pela S1-B** (B-RC-06).
  - Ausente → `reader_pid_namespace_required`.
  - **Qualificação no domínio exato: exigida antes da qualificação de B2**; indisponível agora, e indisponível não é PASS.
- **Nota de evidência sobre B-LIF-09** (correction round 2): o `p2c_term_reader.py` usou um handler que **levanta** `TerminationRequested`, **não** o mecanismo congelado (handler que não levanta + `set_wakeup_fd` + transição do loop para `TEARDOWN`). Portanto:

  ```yaml
  P2c_T1_T2_T6_T7:
    establishes:
      - default SIGTERM behavior is insufficient (T1)
      - SIGTERM is interceptable (T2, T7)
      - controlled cleanup can eliminate survivors (T2, T7)
      - a pidns init ignores SIGTERM from an ancestor without a handler (T6)
    does_not_establish:
      - final non-raising wakeup-fd implementation
      - double-signal safety
      - B-LIF-09 implementation qualification
  B2_must_demonstrate:
    - non-raising signal handler
    - set_wakeup_fd
    - wakeup of a blocked select
    - signal during teardown
    - repeated/double signal
    - zero survivors
  ```
  - **Round 3b:** o P2d exercita experimentalmente o handler que não levanta, o `set_wakeup_fd`, o wakeup do `select` bloqueado, o sinal e o sinal duplo **dentro** do teardown (R3B-S6/S7, sincronizados num marcador observável da fase de varredura) e zero sobreviventes. Isso é `ExperimentalEvidence`; B2 continua qualificando.
- `SIGSTOP` do reader é disponibilidade/DoS (#320), não saída. Crash do host ou do kernel continua não-claim.
- **Escopo do witness:** T5–T7 rodaram num userns sem privilégio (este ambiente), fora do domínio V1, e **sem** comparação de `pidns_identity` (B-RC-06). A semântica do kernel (morte do init do pidns) independe do userns. O witness no domínio exato (launcher privilegiado, pidns no userns inicial) exige root → qualificação de B2/S1-D, `gate_unavailable` aqui.


**Mecanismo selecionado: B, fork/exec explícito** (D-B-SPAWN-MECHANISM-R3, correction round 3, por evidência P2d), dentro de um reader dedicado. O mecanismo A foi rejeitado para o caminho primário V1 (handshake síncrono do `Popen` sem limite). Estas partes são obrigatórias, cada uma com o discriminador que a sustenta:

| # | Elemento obrigatório | Discriminador P2 |
|---|---|---|
| 1 | Pré-condição dedicada: single-thread (`threading.active_count()==1` e uma única task em `/proc/self/task`), verificada; nenhum outro filho | — |
| 2 | Subreaper: `PR_SET_CHILD_SUBREAPER` antes do spawn, verificado por `PR_GET_CHILD_SUBREAPER == 1` | ablação `no_subreaper` + grandchild com `setsid` → sobrevivente |
| 3 | **Bootstrap de sinais do reader** (B-LIF-10, B-LIF-11), na ordem congelada em B-LIF-10 (bloqueia → pipe de wakeup → handlers que não levantam → `set_wakeup_fd` → SIGCHLD normalizado → estado de término pronto → desbloqueia → lê a máscara de volta → inspeciona wakeup e flag → só então admite trabalho). Depois NNP, `fchdir` e census tipado no reader antes do spawn | P2d R3B-S1..S3, R3B-S1-pidns, R3B-C1..C3, CM-R3-04..06; §8, §9, §10 |
| 4 | **Spawn com seção crítica de sinais** (B-LIF-12): o envelope da unidade é fixado antes do `fork`; CONTROLLED bloqueado antes do `fork`; o `fork` fica sob uma guarda que restaura a máscara (e fecha os pipes) se ele falhar; no filho, o **estado de sinais do exec é construído, não herdado** (M1): bloqueia todos os sinais, `set_wakeup_fd(-1)`, **toda disposição do conjunto de sinais endereçáveis pela libc → `SIG_DFL` no kernel** (`sigaction`, nunca o cache do Python), fecha as cópias das pontas de wakeup, máscara de exec vazia para esse conjunto, verifica disposições e máscara lendo do kernel, e **verifica que os bits `SigIgn`/`SigBlk` visíveis no kernel dos sinais reservados à NPTL (32, 33) estão limpos, senão recusa antes do exec** (AB-1); então `dup2` do stdio do protocolo, `RLIMIT_AS`, fechamento dos fds não permitidos, census pré-exec exato e `execve` (env allowlist, argv absoluto, cwd do `fchdir`); a saída do bootstrap é `execve` **ou** relato limitado no canal de erro CLOEXEC + `os._exit`, nunca `raise`/`return`/desenrolar | P2d S-B1..B4, R3C-M1a..d, R3B-G1, R3B-S4/S4b (suporte preliminar), R3B-U1, R3B-F1; P2 (B) |
| 5 | **Identidade:** no pai, `pidfd_open` **imediatamente após o `fork`**, ainda com CONTROLLED bloqueado. Depois restaura a máscara normalizada do reader, e um sinal que chegou durante o bloqueio é entregue nesse ponto, ao handler e ao wakeup. **HANDSHAKE:** `select` em `{setup_error_read, signal_wakeup_read}` sob o deadline (B-RES-02). Nenhuma API reapeia por PID nu | P2d R3B-H1 (1,001 s) vs ablação sem deadline (3,002 s) e A2 (3,002 s); R3B-S5; R3B-H2 |
| 6 | Dono = o reader, por **atribuição do kernel**: a cada rodada, varre `/proc/[pid]/stat` por `ppid == self` (inclui netos reparentados ao subreaper) | ablação `handle_only` → sobrevivente em TF5-B, TF5-C e TF5-F |
| 7 | **Teardown em duas fases** (M2, `OneStuckChildCannotStarveSiblingTeardown`). **Fase 1, atribuição e sinal:** varre todos os filhos atribuíveis, faz `pidfd_open` e a **prova de filiação** (`waitid(P_PIDFD, WEXITED\|WNOHANG\|WNOWAIT)`; um não-filho dá ECHILD e não é sinalizado) e envia `SIGKILL` a **todo** filho vivo atribuído, sem nenhum reap entre os sinais. **Fase 2, reap limitado:** `waitid(P_PIDFD, WEXITED\|WNOHANG)` sobre todos os pidfds possuídos; repete a varredura (netos reparentados), sinaliza os novos e repete o reap, até zero filhos ou o fim do envelope comum. Pelo menos uma passada de atribuição e sinal roda mesmo com o envelope esgotado. **Finalização (M4):** o pidfd do dono e os pidfds candidatos são fechados exatamente uma vez num `finally` externo que cobre varredura, `pidfd_open`, `pidfd_send_signal`, close de candidato e reap | R3C-M2 (dois filhos, um preso), R3C-M2b (neto reparentado), R3C-M4 (5 famílias de falha); R3B-H2; S-B1, W-POS |
| 8 | O teardown roda num `finally` que cobre **toda** saída com filho possuído (§20): falha de setup, falha de exec, timeout do handshake, sinal controlado, `BaseException`, conclusão normal e falha de transporte. Um sinal durante o teardown é registrado e não o interrompe | R3B-S6, R3B-S7 |
| 9 | Fim: nenhum filho restante e `waitid(P_ALL, WNOHANG\|WNOWAIT)` → ECHILD. Caso contrário → `unit_teardown_incomplete`, que o outcome final **deriva do resultado do teardown**, preservando a primária e o pedido de término (§15) | R3B-O1, R3C-M3 |

- **B-LIF-01..12** estão em §16.
- **B-LIF-10. `CONTROLLED_SIGNAL_BOOTSTRAP`** (round 3, finding 4148411723; **ordem corrigida na round 3b**, 4148788381 = N1). `CONTROLLED = {SIGTERM, SIGINT, SIGHUP}`.
  - **Bootstrap do reader, nesta ordem:**
    1. bloqueia CONTROLLED temporariamente;
    2. cria o pipe de wakeup dedicado;
    3. instala handlers que **não levantam**;
    4. `signal.set_wakeup_fd(wakeup_write)`;
    5. normaliza SIGCHLD (B-LIF-11);
    6. marca o estado interno de término como pronto;
    7. desbloqueia CONTROLLED;
    8. lê a máscara de volta; algum sinal de CONTROLLED ainda bloqueado → `reader_signal_mask_not_normalized`;
    9. inspeciona imediatamente o flag de término e os bytes de wakeup;
    10. só então admite trabalho e spawn.
  - **Leis:** `HandlersInstalledBeforeUnblock`; `PendingSignalAtEntry → delivered only after handler/wakeup machinery exists`. A máscara sobrevive ao `exec`, então tratá-la como estado implícito é incorreto (`InheritedSignalMask != AuthorizedControlledExitState`).
  - **Por que a ordem da round 3 era errada:** ela desbloqueava **antes** de instalar os handlers. Com o sinal pendente na entrada, ele era entregue à disposição default: o reader morria (processo comum: rc −15, −2, −1) ou, como init de pidns (o domínio V1), o kernel o descartava em silêncio e o trabalho seguia.
  - **Dono:** a **S1-B** estabelece esse estado localmente; não é autoridade da #350.
  - **Resíduo declarado** (review F5):
    - Um sinal controlado entre o `execve` do reader e o passo 1 atinge a disposição herdada.
    - No domínio V1 (init de pidns), um sinal `SIG_DFL` é descartado pelo kernel; em processo comum, o reader morre, e isso cai no `external_termination_domain`.
    - Só um launcher que entrega CONTROLLED já bloqueado fecharia essa janela. Isso seria uma obrigação nova de #350, **não** criada nesta round. A S1-B **não afirma** controle sobre sinais anteriores ao passo 1 (`may_not_claim`, §28).
  - **P2d:**
    - R3B-S1..S3 (TERM, INT, HUP pendentes na entrada): `terminated` antes de qualquer spawn, com o handler registrando o sinal na fase `ready`. Ablação na ordem da round 3: rc −15, −2, −1, sem resultado.
    - R3B-S1-pidns: `terminated` no mecanismo; na ablação, `completed` com nenhum sinal registrado (descarte silencioso).
    - CM-R3-04..06 (bloqueado na entrada, enviado depois do READY): entregue em 0,111–0,114 s. Ablação sem o passo 7: pendente, o reader só sai em 2,912–2,914 s.
- **B-LIF-11. `CHILD_REAPING_SIGNAL_STATE_NORMALIZED`** (round 3b, N3; adjudicação do mantenedor `S1-B_LOCAL_NORMALIZATION`, [5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387); `InitialSignalDisposition != AuthorizedReapingState`).
  - **Antes de qualquer `fork`:** SIGCHLD → `SIG_DFL`, `SA_NOCLDWAIT` ausente, estado efetivo lido de volta via `sigaction(SIGCHLD, NULL, &old)`. Falha ao estabelecer ou verificar → `reader_sigchld_state_not_normalized`.
  - **Nunca admitidos no estado de spawn:** `SIGCHLD == SIG_IGN` e `SA_NOCLDWAIT`.
  - **Razão:** a S1-B cria o filho e depende de `pidfd_open`, `waitid`, reaping e da semântica de `ECHILD`, então é ela que estabelece o estado de reaping que essas proposições exigem. A proveniência do contexto continua com a #350, que não muda.
  - **P2d:**
    - R3B-C1 (`SIG_IGN` herdado pelo execve) e R3B-C2 (`SA_NOCLDWAIT` na entrada): normalizados → `pidfd_open` ok e o exit observável por `waitid(WNOWAIT)`. Ablação → auto-reap e `pidfd_open` ESRCH.
    - R3B-C3: o filho sai antes do `pidfd_open` → continua zumbi e o pidfd funciona.
    - Mutante que só reseta o handler e mantém `SA_NOCLDWAIT`: recusado pela leitura de volta, antes do `fork`.
  - **Limite:** o Linux zera `sa_flags` no execve, então `SA_NOCLDWAIT` só chega ao reader por estado do próprio processo. No experimento, ele é injetado na entrada.
- **B-LIF-12. `CHILD_BOOTSTRAP_SIGNAL_RESET_AND_TERMINAL_EXIT`** (round 3b, N2 e N6). `ReaderSignalMachinery != ChildBootstrapSignalMachinery`; `ForkChildFailure != ReaderControlFlow`.
  - **Seção crítica do fork:** CONTROLLED é bloqueado antes do `fork`.
    - **Pai:** obtém o pidfd, restaura a máscara normalizada e entra no HANDSHAKE.
    - **Filho: o estado de sinais do exec é CONSTRUÍDO, não herdado** (round 3c, M1; `ChildExecSignalStateIsConstructedNotInherited`, `ReaderSignalState != ChildExecSignalState`):
      1. bloqueia **todos os sinais controlados pela aplicação** (o conjunto endereçável pela libc) durante a construção;
      2. `set_wakeup_fd(-1)`;
      3. para todo sinal capturável **controlado pela aplicação** no domínio de runtime declarado (**exposto à aplicação pela libc/runtime qualificador** (o conjunto endereçável pela libc; no domínio glibc/NPTL, todos exceto SIGKILL, SIGSTOP e os reservados 32 e 33)), a disposição canônica do filho, `SIG_DFL`, **no kernel** vira `SIG_DFL` via `sigaction`. O cache do Python (`signal.getsignal`) nunca é o truth-maker (`PythonSignalCache != KernelSignalDisposition`);
      4. fecha as cópias das pontas de wakeup, **depois** de desregistrar;
      5. máscara de exec vazia para o conjunto endereçável. Não é a máscara herdada do reader (`InheritedSignalMask != AuthorizedExecSignalMask`);
      6. verifica, lendo do kernel, que toda disposição do conjunto endereçável é `SIG_DFL` e que a máscara está vazia. Divergência → relato no canal de erro e `os._exit`;
      7. **sinais reservados à NPTL (32, 33; emenda final, AB-1):** não são capabilities da aplicação (`NPTLReservedSignal != ApplicationSignalCapability`; `LibcAddressableSignals != AllKernelSignalNumbers`).
        - A S1-B **não** tenta sobrescrever essas disposições por syscall crua só para satisfazer a antiga afirmação universal.
        - Antes do exec, os bits `SigIgn` e `SigBlk` desses sinais, visíveis no kernel (`/proc/self/status`), têm de estar **limpos**. Se algum reservado estiver ignorado ou bloqueado → **FAIL CLOSED** com recusa tipada de setup/contexto (nome indicativo `reader_child_signal_state_unusable`; o nome final é detalhe de B2).
        - Lei: `CannotSafelyCanonicalizeReservedSignal → VerifyCompatibleStateOrRefuse`.
        - **Fronteira de TCB** (adjudicação, [5921263805](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5921263805)):
          - os sinais reservados são `libc_internal` e parte confiável do TCB (`S1B_V1_TCB`, §10);
          - a S1-B **não** exige `SigCgt(32/33) == 0`, **não** reescreve essas disposições por `rt_sigaction` cru e **não** verifica a autoria do handler;
          - a checagem de `SigIgn`/`SigBlk` acima é uma pré-condição de compatibilidade fail-closed, **não** detecção de adulteração;
          - adulteração nativa no mesmo processo é comprometimento do TCB declarado (`SameProcessNativeTCBCompromise != ProtectedAdversary`, §28 `may_not_claim`).
      8. só então `dup2`, `setrlimit`, census de fds e `execve`.
    - **Estados distintos:**
      - **reader:** TERM/INT/HUP com handlers que não levantam, pipe de wakeup dedicado e desbloqueio só depois de handlers e wakeup instalados; SIGCHLD `SIG_DFL` sem `SA_NOCLDWAIT`;
      - **filho:** sem registro de wakeup do reader, sem handlers do reader, disposições controladas pela aplicação canônicas (`SIG_IGN` → `SIG_DFL`), máscara de exec canônica (vazia para o conjunto da aplicação), sinais reservados da NPTL deixados ao TCB (checagem de compatibilidade `SigIgn`/`SigBlk` ou recusa).
    - Não depende de o `execve` resetar os handlers: a falha existe justamente **antes** do exec.
    - **Histórico (iteração 2 da 3b, superado pela M1):** o reset de `SIG_IGN` baseado em `signal.getsignal` e a restauração da máscara herdada. A revisão e o Codex mostraram que o cache do Python não vê um `SIG_IGN` nativo e que a máscara herdada leva sinais bloqueados ao Git.
    - **Falha do `fork()`** (Codex 4149311510; round 3c, M3, `ForkFailurePreservesCancellationState`): o `fork` fica sob uma guarda. Se ele falhar, não há filho, e acontece nesta ordem:
      1. restaura a máscara do pai;
      2. fecha os pipes;
      3. o reader **inspeciona o estado de término e o canal de wakeup**;
      4. registra o pedido de término, se houver (`termination_request`, §15);
      5. registra a falha do `fork` (`transport_spawn_failed`, aresta `fork_error` na §20);
      6. deriva o `FailureOutcome` depois do teardown.
      O pedido de término nunca some porque a falha foi levantada logo depois do handler.
    - **Sinal na seção crítica:** um sinal controlado que chega enquanto CONTROLLED está bloqueado no pai fica pendente, é entregue na restauração da máscara e é observado pelo HANDSHAKE (R3B-S8).
    - Resultado: `NoChildCanRunInheritedApplicationControlledReaderHandlerBeforeReset`. Handlers internos confiáveis da NPTL estão explicitamente fora desta proposição.
  - **Bootstrap do filho é terminal:** `execve` com sucesso **ou** relato limitado no canal de erro seguido de `os._exit`. Nunca `raise`, `return`, desenrolar Python, o `finally` do pai ou o código de teardown do reader.
  - **P2d:**
    - R3B-S4 (SIGTERM ao filho depois do reset; **suporte preliminar**, round 3c Q3: o spike não sincroniza com o ponto de reset, então não prova qual lado do reset recebeu o sinal; o witness exato é obrigação de B2): o filho morre por SIGTERM (`CLD_KILLED`/15, observado por `waitid(WNOWAIT)` antes do teardown — é a confirmação da injeção, review F2); o reader não registra nenhum sinal e não reporta término. Ablação sem reset: o handler herdado escreve `0f` no pipe do reader, e o reader reporta `terminated` sem ter recebido sinal (causa misatribuída).
    - R3B-S4b (SIGTERM ao filho **antes** do reset, janela alargada artificialmente): fica pendente e depois toma `SIG_DFL`. Ablação sem o bloqueio do fork: `0f` misatribuído.
    - R3B-G1: o programa exec'ado (`grep`, um programa C; um substituto CPython reignoraria SIGPIPE/SIGXFSZ na própria inicialização) mostra `SigIgn` 0. Ablação sem a construção: 0x1001000.
    - **P2e AB-1** (emenda final): com os estados criados por syscall crua (32 ignorado, 33 ignorado, 32 bloqueado, 33 bloqueado; AB1-CM1..CM4), a checagem congelada recusa com `reader_child_signal_state_unusable`, e o mutante que verifica só o conjunto da libc aceita ("canonicalização fingida"). O `sigaction` da glibc dá EINVAL para o 32. `AB1_evidence: {freeze_requirement: "o estado visível no kernel é checável", implementation_qualification: B2}`.
    - **R3C-M1** (alvo `grep -E '^Sig(Blk|Ign|Cgt):' /proc/self/status`; o spike cobre o conjunto endereçável; a checagem de 32/33 é da emenda e é qualificada em B2): o exec vê `SigBlk` 0 e `SigIgn` 0 em todos os contramodelos. Ablação com o reset da iteração 2 (cache do Python + máscara herdada):
      - SIGPIPE bloqueado na entrada → `SigBlk` 0x1000;
      - SIGXFSZ bloqueado → 0x1000000;
      - `sigaction(SIGUSR1, SIG_IGN)` nativo, invisível ao cache → `SigIgn` 0x200;
      - SIGCHLD ignorado com as duas barreiras desligadas (B-LIF-11 e a construção) → `SigIgn` 0x1011000.
    - **R3C-M3:** `fork()` falha com SIGTERM pendente na seção crítica → primária `transport_spawn_failed` **e** `termination_request` `{signals: [15]}`. Ablação que não inspeciona o estado de término no caminho de erro: `termination_request` nulo.
    - R3B-F1: `fork()` falha → recusa tipada, sem filho, CONTROLLED desbloqueado. Ablação sem guarda: `[1,2,15]` fica bloqueado.
    - R3B-S8: sinal na seção crítica → pendente (`sigpending`) → entregue na restauração → `unit_terminated_by_signal`. Ablação com o handshake sem wakeup: `unit_deadline`.
    - R3B-U1 (`BaseException` no bootstrap): relato `KeyboardInterrupt:…` no canal de erro, `os._exit`, recusa `transport_spawn_failed`, e o cleanup do reader roda **uma** vez, no reader. Ablação em que o filho desenrola: o cleanup roda duas vezes, a segunda no filho.
    - Sem esse discriminador, o stop seria `STOP_CHILD_BOOTSTRAP_UNWIND_UNQUALIFIED`; ele não foi acionado.
- Um grandchild com `setsid` continua no domínio, porque o subreaper (ou o init do pidns) o reparenta ao reader.
- **Prova de filiação (S1B-REV-07):** o ramo "não é filho" (`waitid` → ECHILD) **não foi exercitado** (`not_child_skipped == 0` em todas as execuções admitidas). `ArchitectureMechanismSpecified != ImplementationBranchQualified`: **B2 não pode ser qualificada sem esse witness.**
- PID nu não é identidade. P2 (TF5-G), num userns+pidns privado com `ns_last_pid`: um PID foi **realmente** reusado; o pidfd stale recebeu ESRCH e o processo não relacionado sobreviveu. Na ablação `bare_pid`, o processo não relacionado foi morto.

**Alternativas (correction round 3, D-B-SPAWN-MECHANISM-R3):**
- **A (`Popen` controlado): REJEITADO para o caminho primário V1.**
  - O construtor bloqueia lendo o canal de erro interno até o exec terminar. O chamador não recupera o controle, e nenhum pidfd é obtido durante um stall pré-exec (P2d S-A2: 3,002 s > deadline de 1 s).
  - A não satisfaz B-RES-02 sozinho, e isso não se corrige com timeout posterior.
  - A continua registrado como evidência histórica (P2, P2c) e D-B-CAP-TCB como registro da opção A.
- **B (fork/exec explícito): SELECIONADO.**
  - O dono existe antes do handshake e o deadline global cobre o exec.
  - A herança é provada pelo census do bootstrap.
  - As obrigações do P2 foram **requalificadas para B**, 44/44 em 3 execuções: fechamento de fds, NNP, `RLIMIT_AS` pré-exec, ancoragem por fd, env, falha de setup, falha do pai, `BaseException`, pidfd, netos, subreaper, reuso de PID e zero sobreviventes.
- **C (launcher mínimo): NOT_NEEDED.** B fecha as obrigações sem supervisão externa, então não há novo dono nem nova autoridade.

**Limitações:**
- Processo em estado D não pode ser morto. Um sobrevivente após o deadline é declarado **sem espera bloqueante** (R3B-H2, test double). A testemunha de estado D real não é produzível sem privilégio → `gate_unavailable`.
- **R3B-S4:** o filho morto antes do exec fecha o canal de erro sem relato, e isso aparece como `transport_failed` quando o protocolo não chega. A S1-B não afirma distinguir essa morte de um exec seguido de saída imediata.
- A interface `children` do kernel é ausente no host observado, então a atribuição usa `ppid` + prova de filiação por pidfd. O custo é O(#processos) por rodada.
- O ponto de injeção TF5-B do mecanismo A (histórico) dependia do CPython 3.11 (`subprocess._fork_exec`), e isso era só teste.

## 15. Outcome semantics

- Os reason codes formam um conjunto **fechado e versionado**, fixado em B1, B2 e B3. Os candidatos seguem a numeração da matriz:
  - admissão: `reader_snapshot_not_admitted`, `reader_snapshot_binding_mismatch`, `reader_snapshot_mutable_by_reader`, `reader_snapshot_special_entry`, `reader_object_format_mismatch`;
  - contexto: `reader_context_mismatch`, `reader_context_unobservable`, `reader_procfs_unverified`, `reader_userns_not_initial`, `no_new_privs_unavailable`, `reader_process_not_dedicated`, `reader_fd_capability_unexpected`;
  - lifecycle: `subreaper_required`, `pidfd_unavailable`, `transport_spawn_failed`, `unit_deadline`, `unit_teardown_incomplete`, `reader_descriptor_close_failed`;
  - transporte: `transport_request_invalid`, `transport_header_invalid`, `transport_header_oversize`, `transport_object_missing`, `transport_kind_mismatch`, `transport_truncated`, `transport_framing_invalid`, `transport_object_deadline`, `transport_body_not_admitted`, `transport_envelope_exceeded`, `transport_failed`, `object_hash_mismatch`;
  - toolchain: `toolchain_below_floor`.
  - lifecycle, adicionados na correction round 1: `reader_pid_namespace_required`, `unit_terminated_by_signal`.
  - adicionados na correction round 3: `reader_signal_wakeup_preexisting`, `reader_signal_mask_not_normalized`; `unit_deadline` passa a cobrir também o handshake do exec.
  - adicionados na correction round 3b: `reader_sigchld_state_not_normalized`. O outcome final é **derivado depois do teardown**: teardown incompleto → `dominant_reason = unit_teardown_incomplete`, com a primária preservada (R3B-O1). `unit_terminated_by_signal` passa a cobrir sinais no HANDSHAKE e sinais pendentes na entrada. Um filho que desaparece antes do `pidfd_open` é classificado pelo lifecycle real (`transport_spawn_failed`), sem inventar identidade.
- **Precedência** (D-B-PRECEDENCE): `unit_teardown_incomplete > reader_descriptor_close_failed > primary_failure`, aplicada **só a `dominant_reason`** (`DominantOutcome != OnlyRecordedFailure`, correction round 1, S1B-REV-04).
- **Representação** (os nomes finais podem variar; nenhuma dimensão pode desaparecer):

  ```yaml
  FailureOutcome:
    dominant_reason: <reason code by precedence>
    primary_failure: <typed primary failure | null>              # e.g. transport_header_invalid, unit_terminated_by_signal
    descriptor_close_failure: <typed close failure record | null>
    teardown_failure: <typed survivor/teardown record | null>     # survivors, deadline, D-state
    termination_request: <controlled-signal record | null>        # round 3c (M3): ortogonal; registrado sempre que observado
  ```

  `termination_request` (round 3c, M3) é uma dimensão **ortogonal**: registrada sempre que um sinal controlado foi observado (handler ou byte de wakeup), em qualquer caminho, inclusive falha do `fork`, erros de handshake e **observações durante o TEARDOWN**. Ela não entra na precedência de `dominant_reason`, e nenhuma outra dimensão a apaga ou é apagada por ela.

  **`FinalOutcomeIsDerivedAfterTeardown`** (emenda final, AB-2), linearizado pela **`FINALIZATION_BARRIER`** (adjudicação de fronteira, [5921263805](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5921263805)):

  - **Definição:** depois que o teardown terminou as suas observações, o reader bloqueia CONTROLLED `{SIGTERM, SIGINT, SIGHUP}` de forma atômica. O retorno bem-sucedido dessa transição de máscara é o **ponto de linearização** dos pedidos de término que pertencem à unidade.
  - **Sequência congelada:**

    ```text
    TEARDOWN → block CONTROLLED → FINALIZATION_BARRIER → drain signal_wakeup_read
      → consume termination flags already recorded/delivered → derive final immutable outcome → handoff/publish outcome
    ```

    Nenhum objeto de outcome é congelado antes de a sequência barreira + drenagem terminar.
  - **Posse dos sinais em torno da barreira:**
    - `DeliveredBeforeFinalizationBarrier → belongs_to_current_unit`: um sinal entregue imediatamente antes da barreira, cujo byte ou flag ainda não foi processado, é capturado pela drenagem pós-barreira;
    - `ArrivesAfterFinalizationBarrier → outside_current_unit`: um sinal que só fica pendente depois do bloqueio de CONTROLLED está fora da unidade concluída.
  - **CONTROLLED permanece bloqueado depois da barreira** até o handoff do outcome. Este freeze **não** define reuso do reader, e reuso **não** é assumido: na V1, a S1-B trata a unidade como terminal para o reader, e CONTROLLED fica bloqueado desde a barreira até a saída do reader. Qualquer reuso futuro exige uma fronteira de ciclo de vida própria, definida depois da transferência do resultado. Isto restringe só o comportamento da S1-B e não cria obrigação para o launcher da #350.

  ```text
  COMPLETED  iff  normal work completed
                  AND teardown is complete
                  AND no primary failure exists
                  AND no descriptor-close failure exists
                  AND no teardown failure exists
                  AND no controlled termination request was delivered before FINALIZATION_BARRIER
  ```

  - **Sinal durante o teardown depois de trabalho concluído:** `NORMAL_WORK_COMPLETION → termination signal observed → NOT COMPLETED`. O resultado é `FailureOutcome` com `primary_failure.reason = unit_terminated_by_signal`, `termination_request.signals = [...]`, e os registros de close e de teardown (presentes ou nulos). Não existe `COMPLETED_WITH_SIGNAL`.
  - **Precedência preservada:** `unit_teardown_incomplete > reader_descriptor_close_failed > primary_failure`. Exemplo: conclusão normal + SIGTERM no teardown + teardown incompleto → `dominant_reason: unit_teardown_incomplete`, `primary_failure: unit_terminated_by_signal`, `termination_request: {signals: [SIGTERM]}`, `teardown_failure` presente. Nenhuma informação se perde.
  - **Contagem de dimensões:** a representação normativa tem pelo menos quatro dimensões (`primary_failure`, `descriptor_close_failure`, `teardown_failure`, `termination_request`). A antiga afirmação "3 dimensões / 7 combinações" está **obsoleta**. A tabela abaixo ilustra só a precedência entre as três dimensões que competem por `dominant_reason`; ela **não** é uma enumeração completa. As regras normativas são os invariantes:
    - **(I1)** nenhuma dimensão presente fica `null`;
    - **(I2)** `dominant_reason` segue a precedência;
    - **(I3)** `termination_request` nunca é apagada nem apaga;
    - **(I4)** `COMPLETED` segue a definição acima.
  - **P2e AB-2** (barreira; pipe real + `set_wakeup_fd` + `pthread_sigmask`):
    - sinal entregue antes da barreira → bloqueio + drenagem → `FailureOutcome` (`unit_terminated_by_signal`, `termination_request [15]`); com teardown incompleto → `dominant_reason unit_teardown_incomplete`, primária e término preservados;
    - o mutante que deriva antes do bloqueio e da drenagem devolve `COMPLETED`;
    - um sinal pendente só depois da barreira fica fora da unidade (`COMPLETED`, nada drenado, o sinal continua pendente).

    Isto é um modelo focal da ordem de finalização; a qualificação do reader de produção é de B2. O reader do P2d (spike) ainda captura o término antes do teardown e **não** é evidência desta regra.

  | Ilustração da precedência (não é enumeração completa) | dominant_reason | primary | close | teardown |
  |---|---|---|---|---|
  | só primária | primary | ✓ | — | — |
  | só close | `reader_descriptor_close_failed` | — | ✓ | — |
  | só teardown | `unit_teardown_incomplete` | — | — | ✓ |
  | primária + close | `reader_descriptor_close_failed` | ✓ | ✓ | — |
  | primária + teardown | `unit_teardown_incomplete` | ✓ | — | ✓ |
  | close + teardown | `unit_teardown_incomplete` | — | ✓ | ✓ |
  | primária + close + teardown | `unit_teardown_incomplete` | ✓ | ✓ | ✓ |

  O modelo anterior ("primária preservada") perdia a falha de close sob `unit_teardown_incomplete`. **Contramodelo:** qualquer combinação, com ou sem `termination_request`, em que uma dimensão presente fique `null`, ou `COMPLETED` com pedido de término observado. **Discriminador:** um mutante que colapsa dimensões por precedência; um mutante que deriva o outcome antes do teardown (P2e).
- Não há sucesso parcial. Um objeto entregue antes de uma falha posterior da unidade não é "sucesso da unidade".
- Nenhuma saída da S1-B ativa `S_G`.

## 16. Obligation matrix

Colunas: **ID · proposição · fonte/dono · domínio/aplicabilidade · truth-maker · mecanismo · consumidor real · positivo · contramodelo focal · discriminador/mutação · evidência exigida · limitação · classe.** O consumidor real de todas é a S1-C, e depois a S1-D, via `AuthenticatedGitObjectV2`; hoje não existe consumidor.

| ID | Proposição | Fonte / dono | Domínio | Truth-maker | Mecanismo | Positivo | Contramodelo | Discriminador | Evidência | Limitação | Classe |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B-HO-01 | só um `PublishedSnapshotV2` exato, cunhado e aberto | adjudicação S1-A §5 / S1-B | host | `type is` ∧ registro ∧ `not closed` | API atômica S1-A | snapshot genuíno | spoof, subclasse, não cunhado, residual, fechado | mutante `isinstance`; mutante sem registro | B1 + requalificação focal S1-A | cirurgia intra-processo fora do domínio | MIXED |
| B-HO-02 | dup atômico sob o lock, instalado em dono linear | D-B-HANDOFF / S1-B + S1-A | host | lock + `F_DUPFD_CLOEXEC` + install | API aditiva S1-A | dup == fonte | fd reusado entre get e dup | mutante check→unlock→dup (canário) | B1 | reabre qualificação S1-A | LIVE |
| B-HO-03 | `statx(dup)` == projeção de kernel (5 campos) | D-B-LOCATOR / S1-B | host e reader | `statx` | comparação | identidade igual | fd de outro diretório ou mount | mutante só dev+ino | B1 | snapshot_id e pai são metadata carregada | LIVE |
| B-HO-04 | locator = binding da S1-A | D-B-LOCATOR / S1-B | — | derivação | sem tipo paralelo | — | tipo de locator duplicado | census: tipo paralelo proibido | B1 | — | STATIC |
| B-RC-01 | expectativa explícita, sem default | CONTRACT L137 / S1-B tipo; #350 valores | reader | construtor só com keywords | tipo selado | expectativa válida | expectativa auto-derivada | census: sem `/proc` no construtor | B1 | — | STATIC |
| B-RC-02 | procfs competente | #301 plano / S1-B | reader | `fstatfs` == `PROC_SUPER_MAGIC` | fd de procfs | procfs real | procfs falso (mount) | mutante sem `fstatfs` | B2 | — | LIVE |
| B-RC-03 | ids, grupos, caps, ns e NNP == expectativa | CONTRACT L121–133 / S1-B | reader | syscalls + procfs admitido | comparação antes do spawn | expectativa igual | ids r/e/s/fs inconsistentes, grupo extra, CapAmb ≠ 0 | mutante só euid | B2 (root para ids inconsistentes) | prova consistência, não proveniência | LIVE |
| B-RC-06 | pidns == `pidns_identity` fornecida; procfs ligado ao pidns do reader | D-B-LIFE-PIDNS / S1-B verifica; #350 estabelece | reader | (a) `ns/pid` → `fstat`/`statx` vs expectativa; (b) `f_type` + `/proc/self` == 1 | comparação antes do spawn | contexto de #350 igual à expectativa | pidns não privado; init de **outro** pidns (PID 1 mas identidade divergente); procfs do host; expectativa derivada do próprio reader | mutante só `getpid()==1`; mutante sem comparação de identidade | B2 no domínio exato (root) | proveniência da expectativa é do #350 | LIVE |
| B-RC-04 | userns inicial + expectativa; mntns == expectativa | D-B-USERNS / S1-B | reader | inode de `ns/user` vs constante do kernel | comparação | host sem userns | TF2 (`unshare -rU`) | mutante só uid numérico | B2 (`gate_unavailable` sem userns) | contração V1 | LIVE |
| B-RC-05 | observação ≠ autorização | CONTRACT L137 / #350 | — | — | claim budget | — | apresentar observação como autoridade | review | freeze | — | STATIC |
| B-IMM-01..04 | reader sem autoridade de mutação por inode; só reg/dir; TF1 | CONTRACT L155, L388 (`¬PrincipalCanMutate`) / S1-B | reader | walk por fd + `fstat`/xattr | admissão | **snapshot de outro principal** | mesmo dono, 0666, group-writable, ACL, dir gravável, FIFO, symlink, alias de mount TF1 | mutante `access(path)` | B2 (**exige positivo cross-principal**) | ACL: over-rejection | LIVE |
| B-CAP-01..05 | fds do reader tipados e observados (inclui `signal_wakeup_read/write` e o canal de erro); herança no filho provada pelo census pré-exec do bootstrap (mecanismo B) | adjudicação D-B; round 3 / S1-B | reader e bootstrap do filho (direto) | census tipado do reader + census pré-exec exato do filho + `CLOEXEC` | census pré-spawn + bootstrap | só a allowlist chega; wakeup admitido e não herdado | TF5-H, TF5-I, TF5-J, TF5-J2; CM-W1..W4 | ablação `no_fd_census`; `B_no_close`; P2d CM-W3 (wakeup herdado) | **P2 (B) PASS**; P2d; B2 | premissa TCB: o bootstrap Python do filho após o fork (§10) | LIVE |
| B-PRV-01..04 | caps = 0; NNP antes do exec | CONTRACT L1220–1227 / S1-B | reader e Git | `prctl` + read-back; status do filho | reader dedicado | filho com `NoNewPrivs: 1` | setuid, setgid, file caps, X1, EPERM em kill | **P2: ablação `no_nnp` → 0** | P2 (NNP); B2 (X1 com root) | NNP não bloqueia LSM, userns nem IPC | LIVE |
| B-EXE-01 | acesso ancorado no snapshot admitido | freeze S1-A §19 / S1-B | Git | `fchdir(fd)` + cwd do filho == inode | cwd herdado | cwd == snapshot | rebind de path + decoy | **P2: ablação `path_cwd` → decoy** | P2 PASS; B3 | Git não toca outro arquivo: não afirmado | MIXED |
| B-EXE-02..03, B-EXE-05 | env e args autorados; `safe.directory` por comando; lazy fetch irrelevante | D-B-SAFE-DIR, D-B-GIT-FLOOR / S1-B | Git | dict de env + argv + config do produtor | spawn | `cat-file` funciona | `LD_PRELOAD`, `GIT_ALTERNATE_*`, replace ref, config/hook | mutantes que removem flags; mutante que herda env | P2b; B3 | proveniência do binário é da #350 | MIXED |
| B-EXE-04 | request só com OID completo lowercase | #301 / S1-B | Git | regex de bytes | validação antes do write | OID válido | `HEAD:x`, `C^{tree}`, maiúscula, curto | mutante leniente | B3 | — | STATIC |
| B-TRN-01 | header estrito em bytes | CONTRACT L591 / S1-B | transporte | parser de bytes | máquina de estados | **P2b: 26 headers reais** | 9 do S0 + `+5`, `1_0`, `007`, tab, CRLF, NUL, partido, longo, tipo ou OID divergente, `missing`, `ambiguous` | mutante `int()`/`strip` | P2b; B3 | — | MIXED |
| B-TRN-02 | zero prefetch | Codex 4117693716 / S1-B | transporte | `FIONREAD` == `size+1` na admissão | `os.read(fd,1)` até LF, com teto | body inteiro no pipe | fill de 64 KiB do S0; `BufferedReader.readline`; `read(4096)`; `MSG_PEEK` em pipe | **P2c REV03**: readline e read(4096) → 0/11; bytewise → 11/11; peek → ENOTSOCK | P2c; B3 | — | LIVE |
| B-TRN-03 | admissão como type-state | adendo 5861631976 / S1-B | transporte | estado `BodyAdmitted` | tipo | admitido | cobrança depois da leitura; causa reescrita | mutante que cobra depois de ler | B3 | a closure é da S1-C | LIVE |
| B-TRN-04..05 | framing exato; mesmos bytes | CONTRACT L591 / S1-B | transporte | leitura exata + bytes imutáveis | tipo | objeto normal | truncado, byte extra, sem LF, gotejamento; autenticar A e entregar B | mutante off-by-one; mutante verify-then-reread | B3 | — | LIVE |
| B-AUTH-01..03 | preimage canônica == OID | CONTRACT L507, L586–594 / S1-B | objeto | hashlib sobre os bytes entregues | tipo selado | **P2b: paridade 26/26** | objeto corrompido, replace, pack forjado | **P2b: 6 mutantes de preimage RED** | P2b; B3 | SHA-1: colisão não afirmada; B3 **deve** reusar ou extrair a primitive compartilhada (S1B-REV-05) | MIXED |
| B-RES-01..02 | deadlines de objeto e unidade; **lei `UnitEnvelope = WorkDeadline + TeardownReserve`, `SpawnHandshakeDeadline <= WorkDeadline`, sem espera bloqueante**: envelope fixado antes do fork; o teardown usa o restante do envelope | CONTRACT L582 / S1-B (valores #320) | unidade | relógio monotônico; pidfd antes do handshake; reap só com `WNOHANG` | `select` em `{setup_error_read, signal_wakeup_read}` + loop; teardown limitado | spawn normal dentro do prazo (P2d S-B1) | stall pré-exec (CM-R3-09), erro de setup (CM-R3-10), exec ausente (CM-R3-11), SIGKILL que não completa, trava pós-exec, gotejamento lento, SIGSTOP | P2d: ablação sem deadline no handshake → 3,002 s; A2 → 3,002 s; reap bloqueante → 8,003 s | **P2d R3B-H1 (1,001 s), R3B-H2 (2,503 s num envelope de 2,5 s, `unit_teardown_incomplete`)**; P2 TF5-D; B2/B3 | valores da #320; estado D real não afirmado (test double) | LIVE |
| B-RES-03 | `RLIMIT_AS` antes do exec | CONTRACT L149, L803 / S1-B | Git | `/proc/<pid>/limits` na 1ª ação | bootstrap do filho = `setrlimit` antes do `execve` | limite presente | `prlimit` depois do exec | **P2: ablação → unlimited** | P2 PASS; B2 | agregado não afirmado | LIVE |
| B-RES-04..05 | loop único; stderr sem conteúdo | adjudicação / S1-B | unidade | observação sem conteúdo | loop | stderr drenado | flood de stderr; vazamento de conteúdo | mutante sem drenagem; mutante que publica stderr | B3 | — | MIXED |
| B-RES-06 | R4-3 Policy B | D-B-R43 / S1-B (#320) | unidade | causa observada | checagem proporcional pré-spawn | — | causa inferida | mutante que reporta a causa errada | B3 | mecanismo C fora | MIXED |
| B-LIF-01 | dono antes do filho | CONTRACT L843 / S1-B | unidade | atribuição do kernel | reader + `finally` | teardown limpo | TF5-B (sem handle) | **P2: ablação `handle_only` → sobrevivente** | P2 PASS; B2 | — | LIVE |
| B-LIF-02 | subreaper verificado antes do spawn | CONTRACT L838 / S1-B | unidade | `PR_GET_CHILD_SUBREAPER` | `prctl` | neto reapeado | TF5-F (setsid) | **P2: ablação `no_subreaper` → sobrevivente** | P2 PASS; B2 | — | LIVE |
| B-LIF-03 | identidade via pidfd, nunca PID nu | plano S1 / S1-B | unidade | pidfd + prova de filiação | teardown | — | TF5-G (reuso real) | **P2: ablação `bare_pid` → mata processo não relacionado** | P2 PASS; B2 | exige pidfd no domínio | LIVE |
| B-LIF-04..06 | teardown em toda saída com filho possuído (`EveryOwnedChildPath → TEARDOWN`, §20), **em duas fases** (sinaliza todo filho atribuído antes de qualquer reap; `OneStuckChildCannotStarveSiblingTeardown`); zero sobreviventes; setsid no domínio | CONTRACT L838–846 / S1-B | unidade | varredura por ppid + ECHILD | `finally`; máquina de estados única (§20) | limpo | TF5-A, TF5-C, TF5-D, TF5-E, TF5-F; aresta HANDSHAKE → saída sem TEARDOWN | ablação `handle_only` em TF5-C e TF5-F; mutantes M1–M9 do verificador da §20 | P2 PASS; `p2d_state_machine_check` (lint estrutural); P2d R3B-O1, R3B-F1, **R3C-M2** (dois filhos, um preso: o irmão é sinalizado em 0,001 s e reapeado; ablação serial: sinalizado em 2,475 s e não reapeado), **R3C-M2b** (neto reparentado: 0 vs 1 sobrevivente); B2 | estado D declarado; o filho "preso" é um double da camada de reap | LIVE |
| B-LIF-08 | término externo do reader não deixa sobrevivente | S1B-REV-02 / S1-B verifica; #350 estabelece (D-B-LIFE-PIDNS ADOPT) | unidade | kernel: morte do init do pidns | reader = init de pidns privado | T5: 0 sobreviventes | SIGKILL/crash do reader | T3, T4 (sem pidns / PDEATHSIG) → sobreviventes | P2c; B2 no domínio exato (root) | host crash não afirmado | LIVE |
| B-LIF-09 | sinal de término controlado → teardown, por **canal de wakeup dedicado e tipado**, observado também no HANDSHAKE | S1B-REV-02; round 3/3b / S1-B | reader | handler que **não levanta** + `set_wakeup_fd(write)` + `select(read)` + transição do loop; `set_wakeup_fd(-1)` antes de fechar | loop único; `select` do handshake | P2d W-POS (SIGTERM → wakeup → teardown, 0 sobreviventes, Git sem as pontas); R3B-S5 | SIGTERM sem handler; sinal **dentro** do teardown (R3B-S6); sinal duplo dentro do teardown (R3B-S7); sinal no HANDSHAKE (R3B-S5); canal compartilhado (CM-W6); fechar registrado (CM-W5) | P2d: handler que levanta + sinal dentro do teardown → 1 sobrevivente; handshake sem wakeup → `unit_deadline`; canal compartilhado → byte `0f`; fechar registrado → sentinela escrita | **P2d** (experimento, não qualificação de implementação); B2 qualifica | P2c T2/T7 continuam só evidência de interceptabilidade | LIVE |
| B-LIF-10 | bootstrap de sinais controlados: **handlers e wakeup antes do desbloqueio**, máscara lida de volta, wakeup inspecionado antes de admitir trabalho | round 3 (4148411723); round 3b (4148788381) / S1-B | reader | `pthread_sigmask` + leitura de volta; inspeção do wakeup | reader dedicado single-thread | P2d: pendente na entrada → handler → `terminated` antes do spawn (R3B-S1..S3; pidns) | TERM/INT/HUP pendentes na entrada (CM-SIG-01..03); bloqueados na entrada (CM-R3-04..06) | P2d: ordem da round 3 → rc −15/−2/−1 ou descarte silencioso no pidns; sem desbloqueio → pendente (2,912–2,914 s) | **P2d**; B2 qualifica | a máscara é estado local da S1-B, não autoridade da #350 | LIVE |
| B-LIF-11 | estado de reaping de SIGCHLD normalizado antes de qualquer `fork` (`SIG_DFL`, sem `SA_NOCLDWAIT`, lido de volta) | round 3b (N3), [5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387) / S1-B | reader | `sigaction(SIGCHLD, NULL, &old)` | bootstrap de sinais (passo 5) | P2d R3B-C3: o filho sai antes do `pidfd_open` → zumbi, pidfd ok | `SIG_IGN` herdado (CM-CHLD-01); `SA_NOCLDWAIT` (CM-CHLD-02); filho sai antes do `pidfd_open` (CM-CHLD-03) | P2d: sem normalização → auto-reap, ESRCH; mutante só-handler → recusado pela leitura de volta | **P2d**; B2 qualifica | `SA_NOCLDWAIT` só chega por estado do processo (execve zera `sa_flags`) | LIVE |
| B-LIF-12 | seção crítica de sinais no fork; **estado de sinais do exec construído** (disposições do conjunto endereçável pela libc `SIG_DFL` no kernel, máscara vazia, verificadas; reservados NPTL 32/33 verificados limpos ou recusa); bootstrap terminal (`execve` ou `os._exit`); falha do `fork` preserva o pedido de término | round 3b (N2, N6) / S1-B | bootstrap do filho | máscara bloqueada através do fork; ordem do reset | bootstrap único | P2d R3C-M1a..d: `SigBlk` 0 e `SigIgn` 0 no exec; P2e AB1-CM1..CM4: reservado ignorado/bloqueado → recusa (mutante só-libc aceita); R3B-S4/S4b (preliminar): sinal ao filho não chega ao reader; R3B-U1: cleanup do reader roda uma vez; R3B-F1 e R3C-M3: máscara restaurada e término preservado na falha do `fork` | sinal ao filho antes/depois do reset (CM-R3B-S4/S4b); `BaseException` no bootstrap (CM-R3B-U1); `SIG_IGN` herdado (CM-R3B-G1); `fork()` falha (CM-R3B-F1); sinal na seção crítica (CM-R3B-S8) | P2d: reset da iteração 2 → `SigBlk` 0x1000/0x1000000, `SigIgn` 0x200; sem reset / sem bloqueio no fork → `0f` misatribuído; filho que desenrola → cleanup duplicado no filho; sem guarda → máscara bloqueada; caminho de erro sem inspeção → término perdido | **P2d**; B2 qualifica | a janela fork→reset é alargada artificialmente no experimento | LIVE |
| B-LIF-07 | fds lineares (lei #354), inclusive as pontas de wakeup (posse por fase, §8), o canal de erro, os pipes do protocolo e o pidfd do dono; **guardas de ciclo de vida externas** (`OwnerCapabilitiesClosedOnEveryTeardownPath`, M4) | #354 / S1-B | reader | dono pré-existente, `close_once` | latch padrão S1-A | census de fds limpo | reuso numérico, re-close; wakeup fechado ainda registrado | mutantes do padrão S1-A (M-L3); P2d CM-W5; **R3C-M4** (falha injetada em varredura, `pidfd_open`, `pidfd_send_signal`, close de candidato e reap: pidfd do dono fechado, 0 pidfds e 0 fifos; ablação sem guarda: pidfd do dono vaza) e R3C-M4 dos pipes do protocolo (0 vs 1 fifo) | B1, B2, B3 | — | MIXED |
| B-OUT-01..03 | type-state; reason codes fechados; precedência só em `dominant_reason`; as dimensões preservadas (≥ 4, invariantes I1–I4); `FinalOutcomeIsDerivedAfterTeardown`; sem ativar S_G | CONTRACT §7.1 / S1-B; emenda final (AB-2) | saída | `FailureOutcome` (§15) | derivação depois do teardown; invariantes I1–I4 | — | sucesso com sobrevivente; substituição de causa; dimensão colapsada (S1B-REV-04); sinal no teardown depois de conclusão normal | mutante que colapsa dimensões; mutante que deriva antes do teardown (P2e AB2) | B1, B2, B3 | — | MIXED |
| B-QUA-01..04 | harness sai ≠ 0 em falha; `gate_unavailable ≠ PASS`; census estático e dinâmico | adendo 5861631976 / S1-B | qualificação | exit code do harness; census | harness | — | verde com falha | caso deliberadamente falho | **P2: harness exercitado RED (execuções 1–2)** | Python dinâmico não universal | STATIC |

## 17. Countermodel pack

| Grupo | Contramodelos | Estado |
|---|---|---|
| Handoff | spoof de tipo, subclasse, não cunhado, fechado, residual, fd reusado entre acesso e dup, binding diferente após o dup | a exercitar em B1 |
| Contexto | procfs falso, ids r/e/s/fs inconsistentes, grupo divergente, userns/mntns divergente, expectativa auto-derivada | B2 |
| Imutabilidade | snapshot do mesmo dono, 0666 de outro dono, group-writable, ACL de escrita, dir gravável, FIFO, symlink, alias de mount TF1 | B2 |
| Capability closure | TF5-H (`O_RDWR` inesperado herdado), TF5-I (fd esperado com `O_RDWR`), TF5-J (snapshot como `O_PATH`), TF5-J2 (`O_PATH` extra), `O_RDWR` do launcher | **P2: A e B PASS**; B2 |
| Privilégio | setuid, setgid, file caps, X1 (EPERM em kill) | B2 (NNP já no P2) |
| Hash canônico | omitir tipo, SP ou NUL; tamanho do header; só body; tipo errado | **P2b: 6/6 RED** |
| Confinamento | `PATH` envenenado, `LD_PRELOAD`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, replace refs, config/hook, expressão de revisão, rebind de path | rebind: **P2 PASS**; resto B3 |
| Transporte | 9 headers S0, `+5`, `1_0`, `007`, tab, CRLF, NUL, partido, longo, `missing`, `ambiguous`, prefetch, body antes da admissão, autenticar A e entregar B, byte final errado | `missing` e gramática: P2b; resto B3 |
| Recursos | gotejamento lento, filho travado, SIGSTOP, flood de stderr, backpressure de stdin, memory hog, deadline, R4-3 com causa errada, stderr sensível | TF5-D e rlimit: **P2**; resto B3 |
| Término externo (S1B-REV-02) | SIGTERM sem handler, SIGKILL do reader, PDEATHSIG com neto `setsid`, init de pidns com e sem handler | **P2c T1–T7** (mecanismo de handler do P2c ≠ mecanismo congelado) |
| Sinal controlado (B-LIF-09) | handler que levanta, sinal **dentro** do teardown, sinal duplo dentro do teardown, sinal duplo em RUNNING, select bloqueado não acordado | **P2d** R3B-S6, R3B-S7, R3-double-signal-running (experimento); B2 qualifica |
| Canal de wakeup (round 3; IDs harmonizados na 3b) | CM-W1/W2 wakeup fora da allowlist; CM-W3 wakeup herdado pelo Git (barreiras isoladas: CM-W3a, W3b, W3d; todas removidas: CM-W3c); CM-W4 ponta bloqueante; CM-W5 fechar ainda registrado; CM-W6 wakeup compartilhado com o protocolo | **P2d** |
| Máscara de sinais (round 3) | CM-R3-04 SIGTERM, CM-R3-05 SIGINT e CM-R3-06 SIGHUP bloqueados na entrada do reader | **P2d** (normalizado vs ablação) |
| Sinal pendente na entrada (round 3b) | CM-SIG-01 SIGTERM, CM-SIG-02 SIGINT e CM-SIG-03 SIGHUP bloqueados **e pendentes** na entrada; SIGTERM pendente com o reader como init de pidns | **P2d** R3B-S1..S3, R3B-S1-pidns (ordem 3b vs ordem da round 3) |
| Maquinário de sinais do filho (round 3b) | sinal ao filho depois do reset; sinal ao filho na janela fork→reset | **P2d** R3B-S4, R3B-S4b |
| Sinal no HANDSHAKE (round 3b) | SIGTERM ao reader com o filho parado antes do exec | **P2d** R3B-S5 |
| Estado de reaping (round 3b) | CM-CHLD-01 `SIGCHLD=SIG_IGN` na entrada; CM-CHLD-02 `SA_NOCLDWAIT`; CM-CHLD-03 filho sai antes do `pidfd_open`; mutante de normalização só do handler | **P2d** R3B-C1, R3B-C2, R3B-C3 |
| Handshake limitado (round 3b) | timeout com filho que morre no SIGKILL; SIGKILL que não completa (test double) | **P2d** R3B-H1, R3B-H2 |
| Bootstrap do filho (round 3b) | `BaseException` injetada no bootstrap; `SIG_IGN` herdado do CPython (SIGPIPE, SIGXFSZ); `fork()` falha com CONTROLLED bloqueado; sinal na seção crítica do fork | **P2d** R3B-U1, R3B-G1, R3B-F1, R3B-S8 |
| Outcome (round 3b, iteração 2) | sinal controlado + filho que não morre no SIGKILL: o teardown incompleto tem de dominar | **P2d** R3B-O1 |
| Estado de sinais do exec (round 3c, M1) | SIGPIPE bloqueado na entrada; SIGXFSZ bloqueado na entrada; `sigaction(SIGUSR1, SIG_IGN)` nativo, invisível ao cache do Python; SIGCHLD herdado ignorado | **P2d** R3C-M1a..d (alvo C) |
| Justiça do teardown (round 3c, M2) | dois filhos, o primeiro reportado vivo para sempre pela camada de reap, o segundo matável; neto reparentado atrás de um filho preso | **P2d** R3C-M2, R3C-M2b |
| Falha do `fork` + cancelamento (round 3c, M3) | `fork()` falha com SIGTERM pendente na seção crítica | **P2d** R3C-M3 |
| Finalização de capabilities (round 3c, M4) | falha injetada em varredura, `pidfd_open`, `pidfd_send_signal`, close de candidato e reap; handshake falho com pipes do protocolo abertos | **P2d** R3C-M4 (6 famílias) |
| Máquina de estados (round 3b) | aresta HANDSHAKE → saída sem TEARDOWN; PRIMARY_RECORDED → OUTCOME; handshake sem sinal; filho desenrola para o reader; timeout sem saída; estado REFUSED; aresta de estado do reader para estado do filho; novo estado terminal do tipo filho; timeout rotulado como espera bloqueante | **`p2d_state_machine_check.py`** (mutantes M1–M9) |
| Handshake do spawn (round 3) | CM-R3-09 stall antes do exec; CM-R3-10 erro de setup antes do exec; CM-R3-11 exec ausente | **P2d** (A vs B) |
| Evidência REV01 (round 3) | fd extra vazado deliberadamente após o exec | **P2c** `REV01_mutant_extra_inherited_detected` (witness RED) |
| Identidade de pidns (B-RC-06) | pidns não privado, init de outro pidns, procfs do host, expectativa auto-derivada | **B2** (domínio exato exige root) |
| Informação de falha (S1B-REV-04; emenda AB-2) | combinações de primária, close, teardown e `termination_request`; sinal no teardown depois de conclusão normal | invariantes I1–I4 da §15; P2e AB2; B1–B3 |
| Sinais reservados à NPTL (emenda AB-1) | AB1-CM1 32 ignorado; AB1-CM2 33 ignorado; AB1-CM3 32 bloqueado; AB1-CM4 33 bloqueado | **P2e** AB1 (recusa antes do exec vs mutante só-libc que aceita); B2 qualifica |
| Lifecycle | TF5-A (exceção no setup do filho), TF5-B (filho existe e o construtor falha), TF5-C (`BaseException` pós-spawn), TF5-D (travado antes do protocolo), TF5-E (falha pré-exec), TF5-F (setsid), TF5-G (reuso real de PID), falha de close, estado D, erro primário com sobrevivente | **TF5-A..J: P2 PASS em A e B** (B requalificado na round 3b); estado D real: `gate_unavailable` (lógica do limite: R3B-H2) |

## 18. Positive controls

1. Snapshot legítimo de **outro principal** (B2). Sem ele → `STOP_POSITIVE_CONTROL_UNAVAILABLE`, porque um mecanismo que só recusa não está qualificado.
2. Binding preservado no handoff (B1).
3. Expectativa legítima aceita (B2).
4. Objeto sha1 normal: **P2b**.
5. Objeto sha256 normal: **P2b**, dentro do piso.
6. Objeto em pack e loose: **P2b** (snapshot real da S1-A com pack e loose).
7. Teardown limpo: **P2 A-POS-base e B-POS-base**.
8. Falha limpa que preserva a razão primária: P2 TF5-A e TF5-E (`transport_spawn_failed`).
9. Objeto aceito só após a admissão do body (B3).
10. Os mesmos bytes autenticados chegam ao consumidor (B3).
11. Só a allowlist de fds chega ao reader e ao Git: **P2 POS-base**, com fds do filho = `[0, 1, 2]`.
12. `our_digest == git_generated_oid`: **P2b**, sha1 e sha256.

## 19. Causal mutations

Todo mutante precisa morrer **pelo discriminador pretendido** (`Killed(M) BY IntendedDiscriminator`).

**Observados RED no P2**, cada linha com a injeção confirmada:

| Ablação | Resultado |
|---|---|
| `handle_only` em TF5-B, TF5-C e TF5-F | sobrevivente (A e B) |
| `no_subreaper` em TF5-F | sobrevivente |
| `no_nnp` | filho com `NoNewPrivs: 0` |
| `rlimit_after_spawn` | filho com `unlimited` |
| `path_cwd` + rebind | filho no decoy |
| `bare_pid` em TF5-G | processo não relacionado morto |
| `no_fd_census` | A: reader segura `O_RDWR`, que o `close_fds` esconde do filho; B + `B_no_close`: filho herda o fd |

**Observados RED no P2b:** os 6 mutantes da preimage canônica.

**Observados no P2c** (correction round 1): sem handler de SIGTERM → sobreviventes; PDEATHSIG em vez de pidns → neto sobrevive; `BufferedReader.readline` e `read(4096)` → body consumido; `MSG_PEEK` em pipe → ENOTSOCK.

**A exigir em B1, B2 e B3:**
- mutante `isinstance` e mutante sem registro (B-HO-01);
- mutante check→unlock→dup (B-HO-02);
- mutante só dev+ino (B-HO-03);
- mutante sem `fstatfs` (B-RC-02);
- mutante só euid (B-RC-03);
- mutante com uid numérico (B-RC-04);
- mutante `access(path)` (B-IMM);
- mutante leniente de request (B-EXE-04);
- mutante `int()`/`strip` (B-TRN-01);
- mutante com prefetch (B-TRN-02);
- mutante que cobra depois de ler (B-TRN-03);
- mutante off-by-one (B-TRN-04);
- mutante verify-then-reread (B-TRN-05);
- mutante com timeout por leitura (B-RES-01);
- mutante que publica stderr (B-RES-05);
- mutante com causa inferida (B-RES-06);
- mutantes de precedência e mutante que colapsa dimensões de `FailureOutcome` (B-OUT);
- mutante com handler de sinal que levanta, mais sinal duplo durante o teardown (B-LIF-09);
- mutante sem a verificação da pré-condição de pidns (B-LIF-08); mutante que aceita `getpid()==1` sem comparar `pidns_identity` (B-RC-06);
- witness do ramo "não é filho" da prova de filiação (S1B-REV-07; **obrigatório para qualificar B2**).

**Observados RED no P2d** (correction round 3), cada um com a diferença observada:

| Ablação | Diferença observada |
|---|---|
| A sob stall pré-exec | controle só em 3,002 s |
| B sem deadline no handshake | 3,002 s, contra 1,001 s no mecanismo |
| allowlist antiga | recusa uma implementação conforme |
| as três barreiras removidas (CLOEXEC, reset, laço de fechamento) + sem census | fd 4 (wakeup) herdado pelo Git |
| ponta bloqueante | recusada |
| fechar ainda registrado | sentinela recebe 1 byte |
| canal compartilhado | byte `0f` no stdin do protocolo |
| handler que levanta + sinal dentro do teardown (R3B-S6/S7) | 1 sobrevivente |
| sem desbloqueio da máscara (TERM/INT/HUP) | sinal pendente, sem wakeup (2,912–2,914 s) |
| desbloquear antes dos handlers (ordem da round 3), sinal pendente | rc −15/−2/−1; como init de pidns, descarte silencioso |
| sem reset no filho / sem bloqueio no fork | `terminated` misatribuído, sem sinal no reader |
| handshake sem `signal_wakeup_read` | causa vira `unit_deadline` |
| reap bloqueante após o deadline (test double) | 8,003 s contra 2,503 s (envelope de 2,5 s) |
| `fork()` falha sem a guarda de restauração | CONTROLLED fica `[1,2,15]` bloqueado |
| filho sem reset das disposições `SIG_IGN` | o programa exec'ado herda `SigIgn` 0x1001000 (SIGPIPE, SIGXFSZ) |
| outcome que ignora o resultado do teardown | `terminated` apesar de filho restante |
| handshake sem `signal_wakeup_read`, com sinal na seção crítica do fork | causa vira `unit_deadline` |
| SIGCHLD não normalizado (`SIG_IGN` / `SA_NOCLDWAIT`) | `pidfd_open` ESRCH |
| normalização só do handler | recusada pela leitura de volta |
| filho desenrola em vez de `os._exit` | cleanup do reader duplicado no filho |
| mutantes M1–M9 da §20 | cada um torna uma lei RED |
| reset da iteração 2 (cache do Python + máscara herdada) | `SigBlk` 0x1000 / 0x1000000; `SigIgn` 0x200 no exec |
| teardown serial (pré-3c) | irmão sinalizado em 2,475 s e não reapeado; neto reparentado sobrevive |
| caminho de erro que não inspeciona o término | `termination_request` nulo após falha do `fork` |
| cleanup do dono só no caminho de sucesso | pidfd do dono vaza em cada uma das 5 famílias de falha |
| sem guarda de capability no handshake falho | 1 fifo vazado |

**P2c REV01 (round 3):** o mutante `pass_fds=(extra,)` torna o witness pós-exec RED.

**O harness sai ≠ 0 em falha** (exercitado: execuções 1 e 2 do P2 falharam e foram diagnosticadas; ver `experiments/README.md`).

## 20. Process state machine

**Uma única máquina de estados autoritativa** (round 3b, N5). A lista de arestas abaixo **é** a máquina. Não existe outro diagrama normativo. `experiments/p2d_state_machine_check.py` a lê deste arquivo e verifica as leis sintáticas enumeradas L1–L10; nove mutantes (M1–M9) tornam cada uma RED.

**Claim contraída (round 3c, Q1):** o verificador é **lint estrutural + discriminador de mutação**, e **não** prova de completude. Ele não exclui toda aresta semanticamente contraditória possível. A máquina de estados desta seção continua sendo a **norma**; B2 implementa as transições e testa **todo** caminho terminal (§30.10).

Lei: **EveryOwnedChildPath → TEARDOWN before final outcome.** `OUTCOME` tem um único predecessor, `TEARDOWN`. Estados sem filho também passam pelo `TEARDOWN`, que então só confirma ECHILD, para que a regra não tenha exceção.

<!-- S20-EDGES:BEGIN -->
```text
STATE READER_BOOTSTRAP       owns_child=no
STATE READY_TO_FORK          owns_child=no
STATE FORK_CRITICAL          owns_child=yes
STATE HANDSHAKE              owns_child=yes
STATE RUNNING                owns_child=yes
STATE PRIMARY_RECORDED       owns_child=yes
STATE TERMINATION_REQUESTED  owns_child=yes
STATE UNIT_TIMEOUT           owns_child=yes
STATE TEARDOWN               owns_child=yes
STATE OUTCOME                owns_child=no
STATE CHILD_BOOTSTRAP        owns_child=child
STATE CHILD_EXECVE           owns_child=child
STATE CHILD_EXIT             owns_child=child

EDGE READER_BOOTSTRAP -> READY_TO_FORK : bootstrap_ok
EDGE READER_BOOTSTRAP -> PRIMARY_RECORDED : bootstrap_refusal
EDGE READER_BOOTSTRAP -> TERMINATION_REQUESTED : controlled_signal
EDGE READY_TO_FORK -> FORK_CRITICAL : block_controlled_then_fork
EDGE FORK_CRITICAL -> HANDSHAKE : pidfd_acquired_then_mask_restored
EDGE FORK_CRITICAL -> PRIMARY_RECORDED : fork_error, parent_failure_after_fork, BaseException
EDGE HANDSHAKE -> RUNNING : exec_presumed_on_eof
EDGE HANDSHAKE -> PRIMARY_RECORDED : setup_error, exec_error, BaseException
EDGE HANDSHAKE -> TERMINATION_REQUESTED : controlled_signal
EDGE HANDSHAKE -> UNIT_TIMEOUT : unit_deadline
EDGE RUNNING -> PRIMARY_RECORDED : transport_failure, object_refusal, BaseException
EDGE RUNNING -> TERMINATION_REQUESTED : controlled_signal
EDGE RUNNING -> UNIT_TIMEOUT : unit_deadline
EDGE RUNNING -> TEARDOWN : normal_completion
EDGE PRIMARY_RECORDED -> TEARDOWN : always
EDGE TERMINATION_REQUESTED -> TEARDOWN : always
EDGE UNIT_TIMEOUT -> TEARDOWN : sigkill_via_pidfd_no_blocking_wait
EDGE TEARDOWN -> TEARDOWN : controlled_signal_recorded_not_interrupting
EDGE TEARDOWN -> OUTCOME : finalization_barrier_then_drain_then_derive
EDGE CHILD_BOOTSTRAP -> CHILD_EXECVE : exec_success
EDGE CHILD_BOOTSTRAP -> CHILD_EXIT : bounded_setup_report_then_os_exit
```
<!-- S20-EDGES:END -->

**Semântica dos estados:**

| Estado | O que acontece |
|---|---|
| `READER_BOOTSTRAP` | dedicado; bootstrap de sinais na ordem de B-LIF-10 (inclui B-LIF-11); subreaper; NNP; procfs admitido; expectativa comparada; snapshot admitido; `fchdir`; census tipado |
| `READY_TO_FORK` | o envelope da unidade já foi fixado. Um sinal controlado aqui deixa um byte no wakeup, e a seção crítica o mantém pendente; ele é **observado pelo HANDSHAKE** (R3B-S8), por isso não há aresta própria |
| `FORK_CRITICAL` | CONTROLLED bloqueado; `fork` sob guarda (falha → máscara restaurada → estado de término inspecionado → `fork_error` com `termination_request` preservado); o pai obtém o pidfd e restaura a máscara (B-LIF-12) |
| `HANDSHAKE` | `select` em `{setup_error_read, signal_wakeup_read}` sob o deadline de trabalho (B-RES-02); EOF = exec presumido |
| `RUNNING` | protocolo (request → header → admissão → body → hash)×N |
| `UNIT_TIMEOUT` | SIGKILL via pidfd; a posse passa ao `TEARDOWN`, sem espera bloqueante |
| `TEARDOWN` | pode observar `termination_request` (sinal controlado registrado sem interromper). Duas fases por rodada: (1) varredura por ppid → pidfd → prova de filiação → SIGKILL em **todo** filho atribuído; (2) reap com `WNOHANG` de todos. Repete (netos reparentados) até vazio ou até o fim do envelope; fecha o pidfd do dono uma vez num `finally` externo |
| `OUTCOME` | alcançado **só depois da `FINALIZATION_BARRIER` e da drenagem**: a aresta `TEARDOWN → OUTCOME` é a sub-fronteira explícita (bloqueia CONTROLLED → barreira → drena o wakeup → consome as flags → deriva), sem estado novo e sem mudar a topologia. Derivado: `COMPLETED` ou `FailureOutcome{dominant_reason, primary_failure, descriptor_close_failure, teardown_failure, termination_request}`, conforme a §15 (`FinalOutcomeIsDerivedAfterTeardown`); a topologia não muda |
| `CHILD_*` | só o processo filho: o bootstrap é terminal (B-LIF-12) |

A morte não controlada do reader (SIGKILL, crash) está fora desta máquina: o kernel mata o PID namespace inteiro (pré-condição #350; D-B-LIFE-PIDNS; B-LIF-08).

## 21. FD ownership table

| Descriptor | Criado por | Dono | Liberação | Herdado pelo Git |
|---|---|---|---|---|
| duplicado do snapshot | API atômica da S1-A (B-HO-02) | `ReaderSnapshotInputV2` (dono linear pré-existente) | `close_once` + latch no fim da unidade | não (CLOEXEC; o cwd vem do `fchdir`) |
| fd de procfs | reader | contexto do reader | fim da verificação | não |
| pipes do protocolo | reader | unidade | após o teardown; num handshake falho, fechados antes de propagar (M4) | só a outra ponta como 0/1/2 |
| pidfd do filho | spawn, logo após o `fork` (registro de dono pré-existente) | unidade | `close` **exatamente uma vez** num `finally` externo ao algoritmo de teardown (M4), em todo caminho, inclusive falha de varredura, `pidfd_open`, sinal, close de candidato ou reap; census pós-teardown sem pidfd | não |
| pidfd dos candidatos | teardown | teardown | `close` após o reap | não |
| `/dev/null` somente leitura (se usado) | reader | unidade | fim da unidade | como 0, se for o papel |
| `signal_wakeup_read` / `signal_wakeup_write` | reader (`pipe2(O_NONBLOCK\|O_CLOEXEC)`), no bootstrap de sinais | reader. **Filho pré-exec:** nenhuma ponta depois do reset (`set_wakeup_fd(-1)` e então close das cópias) | `set_wakeup_fd(-1)` → close de cada ponta, exatamente uma vez, mesmo se o teardown levantar | **não** (três barreiras, §8) |
| canal de erro de setup | reader, antes do `fork` | reader (leitura); bootstrap do filho (escrita). O pai fecha sua cópia da escrita logo após o `fork`, antes do `select` | EOF no exec (CLOEXEC) ou na morte do filho, ou após o relato; close único | não |

A lei da #354 vale para todo fd novo: o novo dono é adquirido antes de o anterior poder falhar ao liberar; close no máximo uma vez; falha registrada no latch, nunca re-close.

## 22. Process ownership table

| Processo | Criado por | Dono | Identidade | Fim |
|---|---|---|---|---|
| reader dedicado | #350 (U3), como **init (PID 1) de um PID namespace privado** com mount namespace privado e procfs desse pidns | #350 | `pidns_identity` fornecida pelo host, verificada pela S1-B (B-RC-06) | morte do reader → o kernel encerra o namespace inteiro (B-LIF-08) |
| filho Git | reader (fork/exec explícito, mecanismo B) | reader (atribuição do kernel), desde antes de existir; pidfd logo após o fork | pidfd + prova de filiação | teardown (§14), inclusive durante o handshake do exec |
| netos, inclusive `setsid` | Git ou helper | reader (subreaper) | pidfd + prova de filiação | teardown |

## 23. Census contract

**Helper dedicado:** `tests/agent_review/_s1b_census_v2.py` (D-B-CENSUS). **O census congelado da S1-A não é modificado.**

**Proibições estáticas nos módulos S1-B:**
- `shell=True`;
- cópia de `os.environ`;
- reabrir o snapshot por path;
- `cwd=<snapshot path>`;
- `os.kill`/`killpg` como identidade de teardown;
- `subprocess.Popen` para o filho Git no caminho primário V1 (mecanismo B selecionado);
- qualquer espera pelo handshake do exec fora do `select` sob o deadline;
- CDLL e `prctl` fora da gramática congelada (wrapper único);
- aquisição de descriptor sem dono pré-existente;
- re-close numérico stale;
- bootstrap do filho que faça algo além de: construção do estado de sinais do exec (bloquear todos os sinais, `set_wakeup_fd(-1)`, `sigaction(SIG_DFL)` em todo sinal do conjunto endereçável pela libc, verificação dos bits `SigIgn`/`SigBlk` dos reservados 32/33 com recusa, fechamento das cópias das pontas de wakeup, máscara vazia exata, leitura de volta no kernel), `dup2` do stdio, `setrlimit`, fechamento, census pré-exec, relato pelo canal de erro, `execve` e `os._exit`;
- saída do bootstrap do filho que não seja `execve` ou `os._exit` (`raise`, `return`, desenrolar para o código do reader) (B-LIF-12);
- `fork` sem CONTROLLED bloqueado, reset do filho depois de desbloquear, ou `fork` sem guarda que restaure a máscara se ele falhar (B-LIF-12);
- pidfd do dono não fechado exatamente uma vez no teardown; outcome final decidido antes do resultado do teardown (§15);
- teardown com janela própria em vez do restante do envelope fixado antes do `fork` (B-RES-02);
- desbloquear CONTROLLED antes de instalar os handlers e o wakeup, ou admitir trabalho sem ler a máscara de volta e inspecionar o wakeup (B-LIF-10);
- `fork` sem SIGCHLD normalizado e lido de volta (B-LIF-11);
- espera bloqueante sem limite no domínio da unidade: `waitid` sem `WNOHANG` no caminho do filho, `select`/`read` sem prazo (B-RES-02);
- `select` do handshake sem `signal_wakeup_read`;
- `signal.getsignal` (cache do Python) como truth-maker de disposição; máscara de exec derivada da máscara herdada (M1);
- esperar/reapear um candidato antes de sinalizar todos os atribuídos (M2);
- close do pidfd do dono (ou de capability temporária) fora de um `finally` externo (M4);
- caminho de erro que descarta o estado de término observado (M3); outcome derivado antes das observações finais do teardown (AB-2); outcome congelado antes da `FINALIZATION_BARRIER` e da drenagem; CONTROLLED desbloqueado depois da barreira e antes do handoff (ou, no reader V1, antes da saída);
- sobrescrever disposições dos sinais reservados à NPTL por syscall crua, exigir `SigCgt(32/33) == 0`, ou declarar canonicalizado um estado não verificado (AB-1, fronteira de TCB);
- `set_wakeup_fd` num fd que não seja a ponta dedicada `signal_wakeup_write`; fechar essa ponta sem `set_wakeup_fd(-1)` antes;
- stderr bruto em estado público ou de resultado;
- import de símbolo privado de `bounded_git_v2`;
- objeto de arquivo com buffer (`os.fdopen`, `Popen.stdout.read/readline`) sobre o fd do protocolo (S1B-REV-03);
- handler de sinal que levanta exceção ou faz trabalho além de registrar e acordar o loop (B-LIF-09);
- reimplementação privada da preimage canônica (S1B-REV-05).

**Domínio de cada claim do census:**
- **Estático (AST):** padrões sintáticos (`shell=True`, `os.environ`, `cwd=`, `os.kill`, `subprocess.Popen` para o Git, `os.fdopen` no fd do protocolo, handler que levanta, import privado, conteúdo e saídas do bootstrap do filho, `set_wakeup_fd` fora da ponta dedicada, `waitid` sem `WNOHANG`, ordem do bootstrap de sinais).
- **Estático documental:** as arestas da §20 (`p2d_state_machine_check.py`).
- **Só dinâmico:** fd herdado inesperado, ownership de descriptor, propagação de stderr bruto, autoridade de path do snapshot. Não são afirmados pelo census estático.

**Census dinâmico:**
- fds e descendentes antes e depois;
- conjunto de sobreviventes;
- testemunhas de reuso de descriptor;
- fd herdado inesperado;
- `O_RDWR`/`O_PATH` fora da allowlist;
- falha injetada em cada aquisição, transferência e liberação.

Closure estático ≠ closure dinâmico. Python dinâmico fora do census não é afirmado.

## 24. Toolchain minimums

Regra D-B-GIT-FLOOR: exigir só as versões dos recursos **de fato usados**. Abaixo do piso → recusa tipada (`toolchain_below_floor`), verificada em runtime, nunca presumida.

| Recurso usado | Por quê | Piso |
|---|---|---|
| `git cat-file --batch`, `--no-replace-objects`, `-c` | transporte e hardening | histórico (anterior aos demais pisos) |
| `core.hooksPath` | hardening | Git 2.9 |
| `extensions.objectformat = sha256` | domínio SHA-256 | Git 2.29 (só quando o snapshot declara sha256) |
| `safe.directory` | por comando (§10); **a S1-B não depende da heurística** | não eleva o piso. Em 2.43.0, a invocação com `GIT_DIR` explícito não é submetida à checagem (P2c REV06) |
| `GIT_NO_LAZY_FETCH` | **não usado** (lazy fetch estruturalmente irrelevante) | não eleva o piso |
| kernel pidfd completo | lifecycle | `pidfd_send_signal` 5.1, `pidfd_open` 5.3, `waitid(P_PIDFD)` 5.4 |
| CPython | qualificação | 3.11 (os 3.12 locais não são evidência; ver S1-A §17.4) |

As versões acima são fatos declarados de toolchain. B3 confirma o piso por sonda de runtime e paridade (o P2b observou 2.43.0).

## 25. CI topology

- A #363 é independente. **Não bloqueia P2, P3 nem este freeze docs-only.** Bloqueia a qualificação pesada de B2 e B3.
- Testemunhas que exigem root ou um segundo principal (X1, positivo cross-principal, ids inconsistentes) → CI com sudo sem senha ou dois UIDs; localmente, `gate_unavailable`, nunca PASS.
- Testemunhas que exigem userns sem privilégio (TF5-G, TF2) → `gate_unavailable` onde estiver desabilitado.
- Deadlines de teste na escala de ms; medir as fases do job antes dos grants de B2 e B3.

## 26. Candidate implementation write-set

**NÃO autorizado.**

```text
B1: app/agent_review/reader_snapshot_handoff_v2.py (novo)
    tests/agent_review/test_reader_snapshot_handoff_v2.py (novo)
    app/agent_review/physical_snapshot_v2.py (ADITIVO: só a API atômica; requalificação focal S1-A)
B2: app/agent_review/reader_context_v2.py (novo)
    app/agent_review/contained_git_unit_v2.py (novo)
    tests/agent_review/test_reader_context_v2.py, test_contained_git_unit_v2.py (novos)
B3: app/agent_review/object_transport_v2.py (novo)
    tests/agent_review/test_object_transport_v2.py (novo)
Qualificação: tests/agent_review/_s1b_census_v2.py (novo; D-B-CENSUS)
Possível:     app/agent_review/bounded_git_v2.py (ADITIVO: primitive pública; nunca import de símbolo privado)
```

## 27. B1/B2/B3 slicing

| Slice | Entrega | Claims | Não fecha | Qualificação |
|---|---|---|---|---|
| **B1: handoff e admissão do snapshot** | API atômica S1-A, `ReaderSnapshotInputV2`, tipos de `ReaderContextExpectationV2` | B-HO-*, B-RC-01, B-RC-05, B-LIF-07 parcial | contexto autorizado, TF1/TF2, NNP, lifecycle do Git | suíte B1 + requalificação focal S1-A |
| **B2: contexto do reader + capability closure + privilégio + lifecycle** | procfs, comparação, imutabilidade, census tipado, NNP, subreaper, bootstrap de sinais (B-LIF-10/11), spawn B com bootstrap fork/exec explícito e estado de sinais do exec construído (§14, B-LIF-12), teardown limitado em duas fases; **qualifica todo item de `B2_MANDATORY_QUALIFICATION_GAPS` (§30.10)**; roda primeiro contra um filho falso | B-RC-02..04, B-IMM-*, B-CAP-*, B-PRV-*, B-LIF-* | `cat-file` | **positivo cross-principal obrigatório**; #363 resolvida |
| **B3: Git contido + transporte + autenticação de objetos** | `cat-file --batch` relativo ao fd, máquina de estados, deadlines, stderr, R4-3, hash | B-EXE-*, B-TRN-*, B-AUTH-*, B-RES-*, B-OUT-* | closure (S1-C) | paridade com o Git real; #363 resolvida; todo wait/read usa `remaining(absolute_deadline_at)` (§30.10) |

Cada slice tem **grant próprio**, na ordem B1 → B2 → B3. Este freeze não implementa nenhuma.

## 28. Claim budget

```yaml
may_claim:   # após a qualificação de cada slice, nunca por este freeze
  - genuine_published_snapshot_handoff
  - descriptor_binding_preserved_across_handoff
  - reader_context_observation_against_explicit_expectation
  - reader_non_mutability_mechanism_within_declared_domain
  - inherited_fd_capability_closure_by_child_bootstrap_pre_exec_census   # mechanism B (V1): direct pre-exec census + CLOEXEC (B-CAP-05)
  - unit_deadline_covers_exec_handshake                             # B-RES-02, mechanism B
  - controlled_signal_mask_normalized                               # B-LIF-10
  - controlled_signal_handlers_installed_before_unblock             # B-LIF-10 (round 3b)
  - child_reaping_signal_state_normalized                           # B-LIF-11
  - child_exec_signal_state_constructed_and_kernel_verified          # B-LIF-12, M1: conjunto endereçável pela libc; reservados NPTL verificados ou recusa (AB-1)
  - final_outcome_derived_after_teardown                              # §15, AB-2
  - final_outcome_linearized_at_finalization_barrier                  # §15, adjudicação de fronteira
  - child_bootstrap_terminal_exit                                    # B-LIF-12
  - teardown_fairness_signal_all_before_wait                         # M2
  - cancellation_preserved_across_fork_failure                       # M3
  - owner_capabilities_finalized_on_every_teardown_path              # M4
  - no_unbounded_blocking_wait_in_unit                              # B-RES-02 (logic; real D-state behaviour not claimed)
  - exec_privilege_non_escalation_mechanism
  - descriptor_relative_contained_git_execution
  - strict_git_object_transport_framing
  - zero_body_prefetch_before_admission
  - per_object_resource_enforcement_mechanism
  - lifecycle_ownership_from_spawn                    # controlled_exit_domain + external_termination_domain (pidns precondition, D-B-LIFE-PIDNS ADOPT); B-LIF-09 qualified only in B2
  - zero_survivors_or_typed_refusal
  - failure_information_preserved_across_precedence   # FailureOutcome, >= 4 dimensions (invariants I1-I4)
  - content_address_authentication_of_delivered_object_bytes
may_not_claim:
  - AuthorizedReaderExecutionContext_provenance
  - ExpectedCommit_provenance
  - C2_B_COMPLETE
  - authenticated_commit
  - authenticated_root_tree
  - authenticated_closure
  - authenticated_S_G
  - S1_COMPLETE_AS_COMPONENT
  - C4_COMPLETE
  - G5
  - universal_DOS_resistance
  - aggregate_memory_bound
  - host_toolchain_provenance
  - host_compromise_resistance
  - git_touches_no_other_host_file
  - direct_pre_exec_fd_proof_under_mechanism_A
  - real_D_state_kill_behaviour
  - controlled_signal_before_reader_bootstrap_step_1   # resíduo declarado (B-LIF-10, review F5)
  - semantic_completeness_of_state_machine_checker     # Q1: lint estrutural + mutação
  - two_independently_observed_signals_during_teardown # Q2: B2
  - child_post_reset_signal_witness                    # Q3: B2
  - post_exec_stall_under_absolute_unit_deadline       # Q4: B2/B3
  - implementation_qualified                           # ArchitectureMechanismSpecified != ImplementationQualified
  - canonicalization_of_nptl_reserved_signals          # AB-1: only verified-compatible-or-refuse
  - reserved_signal_handler_authorship                 # TCB boundary: libc_internal, trusted
  - detection_of_same_process_raw_syscall_tampering_of_nptl_reserved_signals   # SameProcessNativeTCBCompromise != ProtectedAdversary
  - reader_reuse_semantics                             # not defined in V1; not assumed
  - experiments_qualify_B2                             # P2/P2b/P2c/P2d/P2e are design evidence only
  - survival_of_host_or_kernel_crash
  - production_ready
  - PROVED
```

## 29. D-B adjudications

Adjudicadas pelo mantenedor em #301 [5905959720](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5905959720). São `MaintainerAdjudication`, não recomendação do agente.

| D-id | Disposição | Decisão congelada aqui |
|---|---|---|
| D-B-46 | ADOPT | roadmap reconciliado |
| D-B-U3-OWNER | ADOPT, dona = #350 | fronteiras em §2; aceitação registrada na #350 (5905962787) |
| D-B-HANDOFF | ADOPT | `atomic_admit_dup_install_under_S1A_lock` (§6); **requalificação focal da S1-A exigida** |
| D-B-LOCATOR | ADOPT | derivado de `PublishedSnapshotBindingV2`; projeção de kernel (5) e metadata (2); tipo paralelo proibido |
| D-B-SPAWN-MECHANISM | DEFER_TO_CAUSAL_SPIKE → P2: A selecionado → **reaberto no round 3 e substituído por D-B-SPAWN-MECHANISM-R3 (B)** | §14 |
| D-B-USERNS | AUTHORIZED_CONTRACTION | `initial_user_namespace_only` (V1); não é lei universal |
| D-B-GIT-FLOOR | ADOPT | lazy fetch estruturalmente irrelevante (P2b); piso por recurso usado (§24) |
| D-B-SAFE-DIR | ADOPT | escopo de comando mínimo (5905959720) |
| **D-B-SAFE-DIR-VALUE** | **ADOPT** ([5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)) | `-c safe.directory=*` numa única invocação autorizada do Git; configuração ambiente proibida; a heurística de ownership do Git **não** é autoridade da fronteira da S1-B; o comportamento do Git 2.43 com `GIT_DIR` explícito é só evidência de suporte |
| **D-B-CAP-TCB** | **AUTHORIZED_CONTRACTION** ([5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)); **HISTÓRICO desde o round 3** | registro da opção A (census do reader + premissa TCB do subprocess do CPython 3.11); **não é load-bearing** no caminho primário V1, que agora é B |
| **D-B-SPAWN-MECHANISM-R3** | **ADOPT_B_FOR_V1** (adjudicação condicional do mantenedor, [5918385379](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5918385379); predicados demonstrados no P2d); **mantido na round 3b, `reopen: false`** ([5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387)) | `selected: explicit_fork_exec`; `reason: bounded_ownership_exists_before_exec_handshake_completion`; A `REJECTED_FOR_V1_SELECTED_PATH` (`unbounded_synchronous_Popen_handshake`); C `NOT_NEEDED` |
| **N3_SIGCHLD_OWNER** | **S1-B_LOCAL_NORMALIZATION** ([5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387)) | B-LIF-11; `InitialSignalDisposition != AuthorizedReapingState`; a #350 não muda (proveniência do contexto continua dela) |
| D-B-R43 | ADOPT | Policy B; `ReportedCauseRequiresObservedTruthMaker`; números da #320 |
| D-B-PRECEDENCE | ADOPT | `unit_teardown_incomplete > reader_descriptor_close_failed > primary_failure`; diagnóstico primário preservado |
| D-B-CENSUS | ADOPT | helper S1-B dedicado; census S1-A intocado |
| D-B-CI | ADOPT | #363 independente; bloqueia só a qualificação pesada de B2 e B3 |
| **D-B-LIFE-PIDNS** | **ADOPT** ([5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542)) | `decision: reader_is_pid1_of_private_pid_namespace`; `userns: initial`; `mountns: private`; `procfs: bound_to_reader_pidns`; `established_by: "#350"`; `verified_by: S1B`; `exact_domain_positive: required_before_B2_qualification` |

**Nota:** a leitura de D-B-SAFE-DIR (escopo = uma invocação `-c`, valor `*`) foi reavaliada na correction round 1 (S1B-REV-06). O truth-maker são as propriedades próprias da S1-B (§10), e não a heurística do Git.

## 30. Stops and final disposition

### 30.1 P2 (spike causal de spawn, experimento descartável)

```yaml
P2_DISPOSITION:
  SPAWN_MECHANISM_SELECTED:
    mechanism: "A — controlled Popen inside a dedicated reader (HISTORICAL: superseded in correction round 3 by D-B-SPAWN-MECHANISM-R3 = B)"
    evidence: "experiments/p2_spawn_spike_results.json — 44/44 PASS (runs 3, 4 and 5 all 44/44; the admitted JSON was regenerated in correction round 3 with the deadline-bounded B, 44/44 x3); experiments/p2b_git_facts_results.json — PASS sha1 and sha256"
    alternatives_rejected:
      B_fork_exec: "HISTORICAL (P2): closes the same obligations with a larger re-implemented child path; retained as fallback — SELECTED in correction round 3"
      C_minimal_launcher: "no demonstrated need (LargerMechanismRequiresDemonstratedNeed)"
    unavailable_witnesses: [D_state_process (requires privilege), X1_setuid_filecaps (B2), cross_principal_positive (B2)]
    qualification_transfer: none
    correction_round_1:
      A_retained: true
      A_fd_inheritance: "reader side observed; child-side closure delegated to CPython (TCB premise), not directly proved"
      B: "HISTORICAL (round 1): fallback if a direct in-child pre-exec proof becomes required — SELECTED in correction round 3"
      lifecycle: "external termination closed by pidns-init precondition (P2c T5); D-B-LIFE-PIDNS ADOPT (5915365542)"
    correction_round_2:
      D-B-CAP-TCB: "AUTHORIZED_CONTRACTION: functional unchanged, assurance contracted to the CPython TCB premise"
      B-LIF-09_evidence: "P2c used a raising handler; it does not qualify the frozen non-raising wakeup-fd mechanism (B2)"
    correction_round_3:
      D-B-SPAWN-MECHANISM-R3: "ADOPT_B_FOR_V1 — P2d: A pre-exec stall 3.002s (unbounded handshake); B bounded 1.002s; B setup/exec failures bounded; P2 re-run for B 44/44 x3"
      evidence: "experiments/p2d_spawn_handshake_results.json (26/26 x3); experiments/p2_spawn_spike_results.json regenerated with the deadline-bounded B (44/44 x3); experiments/p2c_corrections_results.json (14/14 x3, REV01 discriminating)"
      disposition_at_a727c7e: "NOT_CONVERGED (#301 5919204387)"
    correction_round_3b:
      spawn: "B kept (reopen: false); fork signal critical section; child reset; terminal child bootstrap; wakeup-aware handshake; no blocking wait after the deadline"
      evidence_round_3c: "p2d_spawn_handshake_results.json (91 rows + 31 evidence contracts + claims block, x3); p2_spawn_spike_results.json (44/44 x3); p2c_corrections_results.json (14/14 x3)"
      evidence: "p2d_spawn_handshake_results.json (64 rows + 18 evidence contracts, x3, iteration 2); p2_spawn_spike_results.json (44/44 x3, B re-run with round-3b spawn_B); p2c_corrections_results.json (14/14 x3); p2d_state_machine_results.json (laws L1-L10 GREEN, mutants M1-M9 killed); P2b re-run on b3657d3: identical"
```

**Matriz P2** (mecanismo × obrigação × positivo, contramodelo e discriminador × resultado; A e B idênticos):

| Obrigação | Positivo | Contramodelo | Discriminador (ablação RED) | A | B |
|---|---|---|---|---|---|
| OwnerEstablishedBeforeChildExists / NoSpawnToUnownedWindow | POS-base | TF5-B | `handle_only` → sobrevivente | PASS | PASS |
| TeardownCoversEveryExit | POS-base | TF5-A, TF5-C, TF5-D, TF5-E | `handle_only` em TF5-C → sobrevivente | PASS | PASS |
| SubreaperEstablishedBeforeSpawn | TF5-F reapeado | TF5-F | `no_subreaper`; `handle_only` → sobrevivente | PASS | PASS |
| PidIdentityNotBarePid | — | TF5-G (reuso **real**) | `bare_pid` → processo não relacionado morto | PASS | PASS |
| DescriptorInheritanceClosed | fds do filho `[0,1,2]` | TF5-H, TF5-I, TF5-J, TF5-J2 | `no_fd_census` (A: reader `O_RDWR`; B+`no_close`: fd herdado) | PASS (lado do reader; filho **delegado ao TCB**) | PASS (direto no filho) |
| NNPBeforeExec | `NoNewPrivs: 1` | — | `no_nnp` → 0 | PASS | PASS |
| RlimitBeforeExec | limite na 1ª ação | — | `prlimit` pós-spawn → unlimited | PASS | PASS |
| DescriptorRelativeSnapshotBinding | cwd == inode do fd | rebind + decoy | `path_cwd` → decoy | PASS | PASS |

### 30.2 P3 (Structural Change Preflight §§1–7)

Estas são as respostas na versão viva em `ab92e89`. Os predicados epistêmicos vêm só de §"Epistemic classification" do preflight.

**§1 Property**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Propriedade externa | um reader estabelecido externamente consome o snapshot S1-A com ancoragem por fd, sem ganho de privilégio, Git contido, transporte estrito, objetos autenticados por conteúdo e lifecycle possuído desde o spawn (os 11 domínios) | §16 | P2, P2b | §17 | #301 e CONTRACT | por slice |
| Observação mecânica | `statx`, procfs admitido, census tipado, status e limites do filho, varredura ppid + pidfd, parser de bytes, hash | idem | P2, P2b | idem | kernel e Git | B1, B2, B3 |
| Disposição conservadora | recusa tipada e teardown com precedência; nunca operação degradada | §15 | P2 (precedência) | sucesso com sobrevivente | D-B-PRECEDENCE | B2, B3 |

**§2 Authority**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Quem já possui cada regra | gramática do `cat-file --batch` e preimage canônica: **Git real**; identidade do snapshot: S1-A (binding); contexto: #350; semântica de closure: S1-C | registro de donos (§2) | busca abaixo | — | adjudicação D-B | — |
| Deriva ou reimplementa | **deriva** a identidade do snapshot do binding da S1-A e do `statx` (sem locator paralelo); **projeta** a gramática e a preimage do Git com **paridade** contra o Git real (se o Git mudar, a paridade falha, não fica stale em silêncio); não reimplementa a leitura de packs (KEEP_GIT) | P2b | P2b PASS | pack reader próprio | CONTRACT decisão a | B3 (paridade) |
| Contagem de autoridades antes → depois | autoridades semânticas: **1 → 1** (Git; S1-A; #350). **Projeções no repo** da gramática do `cat-file --batch`: 1 → 2 (C3, leniente, + S1-B estrita). Da preimage canônica (**recontado na correction round 1**): **construtores** 1 (`strict_json.git_blob_oid`, só blob sha1) e **verificadores de hash** 1 (`_verify_loose_object_hash_v2`, que faz hash do objeto loose inflado e **não** constrói a preimage). A contagem anterior "2 → 3" misturava as duas coisas. **B3 deve reusar ou extrair a primitive compartilhada**, nunca uma terceira reimplementação privada (§12, S1B-REV-05). A C3 continua não reutilizável porque é leniente (F4) | busca | ver abaixo | projeções que divergem | CONTRACT | paridade por projeção |

Busca que sustenta as negativas (escopo `app/` e `scripts/` em `ab92e89`):
- nenhum consumidor de `PublishedSnapshotV2` fora de `physical_snapshot_v2.py`;
- parsers de `cat-file --batch` só em `git_commit_subject_v2.py`;
- preimage construída só em `app/common/strict_json.py:142` (blob, sha1); hash de loose inflado em `trusted_object_authority_v2.py:818`;
- nenhum NNP ou subreaper em `app/`; pidfd só no subsistema #201.

**§3 Language / capability**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Aceitos explicitamente | Linux com pidfd completo; x86_64/aarch64; CPython 3.11; userns inicial; sha1 e sha256 (piso); objetos commit, tree, blob e tag; loose e pack | §4, §24 | P2, P2b | — | D-B-USERNS, D-B-GIT-FLOOR | B2, B3 |
| Não suportados, e onde está escrito | userns não inicial, rootless, kernel sem pidfd, Git abaixo do piso, estado D (declarado), NFS/FUSE (#320): §4, §13, §14 e §24 deste freeze | idem | — | — | adjudicação | — |
| Decisão implícita restante | nenhuma identificada. O valor de `safe.directory` foi adjudicado pelo mantenedor (D-B-SAFE-DIR-VALUE, 5915365542) | — | — | — | — | revisão independente |

**§4 Positive / negative corpus**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Rejeitar | §17 | — | TF5-A..J (P2), mutantes da preimage (P2b) | §17 | — | B1, B2, B3 |
| Passar, com **igualdade** | cada objeto real: `(oid, kind, body)` com `digest == oid do Git` (igualdade, não "não levanta"); limite declarado: o corpus P2b cobre 13 objetos × 2 formatos | P2b | P2b PASS | over-rejection | Git | B3: corpus ampliado |
| Paridade com o upstream | sim: `cat-file --batch` real sobre um snapshot real da S1-A | P2b | PASS | — | Git | B3 |

**§5 Evidence / mutation discrimination**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Teste que representa | harness P2 (lifecycle, capabilities, NNP, rlimit, ancoragem por fd) e P2b (transporte, hash); em B1, B2 e B3, suítes próprias por obrigação (§16) | — | P2, P2b | — | — | B* |
| Mutação executada e observada RED | sim, cada ablação e cada mutante listado em §19 foi **observado** RED, com a injeção verificada. Execução 2 falhou porque a injeção TF5-B não ocorria, o que foi detectado e corrigido (README) | §19 | resultados | kill acidental | preflight §"Causal mutation discrimination" | — |
| Predicados epistêmicos | para as proposições do P2 no domínio declarado (host observado, CPython 3.11, Git 2.43.0): `DEFINED`, `MECHANICALLY_VERIFIED` (limitado ao domínio), `MUTATION_DISCRIMINATED`, `EMPIRICALLY_SUPPORTED` (corpus finito). `NON_REFUTED` ainda não: só após revisão com escopo declarado. **`PROVED` indisponível**: não há base de prova externa | — | — | — | preflight | revisão independente |

**§6 Cross-layer assumptions**

| Suposição ("always/never/guarantees") | Autoridade ou teste que a sustenta |
|---|---|
| exec fecha todo fd com `FD_CLOEXEC` | semântica do kernel (TCB); corroborado pelo P2 (fds do filho = `[0,1,2]`) |
| o `_posixsubprocess` só faz `dup2` 0–2 → `preexec` → `close_fds` → exec | **histórico** (mecanismo A); não é load-bearing em V1 |
| o bootstrap do filho (B) deixa exatamente `{0,1,2}` + canal de erro CLOEXEC antes do `execve` | **inspecionado** pelo próprio bootstrap (census pré-exec); P2 (B) + ablação `B_no_close` |
| exec preserva uma máscara de sinais bloqueada | kernel; **P2d** (ablação sem normalização → sinal pendente); a S1-B normaliza localmente (B-LIF-10) |
| `set_wakeup_fd` exige ponta não bloqueante | CPython 3.11 (`ValueError`); **P2d CM-W4** |
| sinais pendentes sobrevivem ao execve; `fork()` zera o conjunto pendente do filho | kernel; **P2d** R3B-S1..S3 (pendência criada pelo launcher antes do execve) |
| o init de um pidns descarta um sinal `SIG_DFL` vindo de dentro do namespace | kernel; **P2d** R3B-S1-pidns (ablação) |
| `SIGCHLD=SIG_IGN` sobrevive ao execve; `sa_flags` são zerados; `SIG_IGN`/`SA_NOCLDWAIT` → auto-reap | kernel; **P2d** R3B-C1/C2; sonda do round 3b |
| handlers Python e o registro do wakeup são herdados pelo `fork` | CPython 3.11; **P2d** R3B-S4/S4b (ablações) |
| `signal.getsignal` reflete um cache do CPython, não o kernel (um `sigaction` nativo fica invisível) | CPython 3.11; **P2d** R3C-M1c |
| a máscara de sinais sobrevive ao `execve` | kernel; **P2d** R3C-M1a/b (ablação) |
| a morte do init de um pidns mata todo o namespace | kernel; **P2c T5** (fora do domínio V1 por causa do userns; B2 no domínio exato) |
| a heurística `safe.directory` não se aplica a `GIT_DIR` explícito | observado no Git 2.43.0 (P2c REV06); **não usado** como autoridade |
| o subreaper recebe netos órfãos | kernel; **P2 TF5-F + ablação** |
| waitid em pidfd de não-filho → ECHILD | kernel; usado na prova de filiação; o P2 não exercitou um candidato não-filho (`not_child_skipped = 0`), **gap declarado para B2** |
| zumbi mantém o PID reservado até o reap | kernel; P2 TF5-G mostra o reuso só após o reap |
| o Git não faz lazy fetch sem promisor ou remote | config do produtor (P2b) + C5 da S1-A + env e argv autorados |
| o Git não reabre o snapshot por path | **não afirmado**; só a ancoragem do cwd (B-EXE-01) |
| o produtor não muta o snapshot após a publicação | premissa da #350 (U3) |

**§7 Snapshot / ownership**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Leituras de uma decisão vêm de um único snapshot | sim: o header é parseado uma vez de um buffer; os bytes hashados são os entregues; um fd por dono, com identidade conferida após o dup; o contexto vem de uma vista de procfs admitida | §11, §6 | P2b | autenticar A e entregar B | — | B3 |
| Outro consumidor reinterpreta a mesma fonte | C3 (`git_commit_subject_v2`) lê `cat-file --batch` com regra leniente própria. **Divergência declarada**, fora do escopo da S1-B (follow-up de consolidação) | busca (§2) | — | — | C3 é outra superfície | — |
| Regra copiada para segunda tabela | sim, e declarada: a gramática e a preimage são projeções do Git, com paridade; o env e os args reutilizam `bounded_git_v2` público onde coincidem | P2b | PASS | deriva silenciosa | Git | B3 |

```yaml
P3_DISPOSITION:
  unknowns: 0
  stop_triggered: none
  authority_count_before_after: {semantic: "1 → 1", in_repo_projections: {cat_file_batch_grammar: "1 → 2", canonical_preimage_constructors: "1 → 1 (B3 MUST reuse/extract shared primitive)", loose_hash_verifiers: "1 (unchanged)"}}
  copied_or_projected_git_semantics:
    cat_file_batch_transport_grammar: {authority: real_git, qualification: parity_with_real_git}
    canonical_git_object_preimage: {form: "<type> SP <decimal-size> NUL <body>", authority: real_git, qualification: parity_with_real_git}
  own_pack_reader: false
  declared_gaps_for_B2: [waitid_ECHILD_non_child_candidate_witness (mandatory for B2 qualification), double_signal_during_teardown, pidns_witness_in_exact_V1_domain (root)]
  pending_decisions: []          # D-B-LIFE-PIDNS, D-B-CAP-TCB and D-B-SAFE-DIR-VALUE adjudicated (5915365542)
  declared_gaps_for_B2_added_round_2: [B-LIF-09 final mechanism witnesses, B-RC-06 pidns identity in the exact domain]
  correction_round_3: {unknowns: 0, stop_triggered: none, new_owner: none, new_authority: none, tcb_shift: "CPython subprocess premise (A) → child bootstrap inspected pre-exec (B)"}
  correction_round_3b: {unknowns: 0, stop_triggered: none, new_owner: none, new_authority: none, owner_rulings: {SIGCHLD_normalization: S1-B, signal_mask_normalization: S1-B, reader_context_provenance: "#350", pidns_mntns_procfs_establishment: "#350"}, tcb_additions: "signal ops in the child bootstrap; kernel pending/exec/pidns-init/SIGCHLD semantics (§10)"}
```

### 30.3 Convergence boundaries (para o loop de revisão)

```text
subject identity; semantic authority (Git vs projeções); authority vs evidence (observação ≠ autorização);
representation fidelity (bytes hashados == entregues); lifecycle derivation (atribuição do kernel);
runtime-behaviour derivation (premissas TCB); evidence qualification (experimento ≠ qualificação);
claimed vs observed verification domain (host P2, CPython 3.11, Git 2.43.0)
```

### 30.4 Stop conditions (ativas a partir daqui)

```text
STOP_SUBJECT_DRIFT, STOP_OWNER_BOUNDARY, STOP_UNRESOLVED_POLICY, STOP_POSITIVE_CONTROL_UNAVAILABLE,
STOP_S1B_REQUIRES_READER_CONTEXT_ESTABLISHMENT, STOP_S1B_REQUIRES_CLOSURE_SEMANTICS,
STOP_S1B_REQUIRES_EXPECTED_COMMIT_PROVENANCE, STOP_S1B_REQUIRES_C2B_IMPLEMENTATION,
STOP_S1B_REQUIRES_S1A_SEMANTIC_CHANGE, STOP_S1B_REQUIRES_OWN_PACK_READER, STOP_S1B_REQUIRES_201_CONTRACT_CHANGE,
STOP_SPAWN_LIFECYCLE_UNCLOSED, STOP_TOOLCHAIN_FLOOR_UNRESOLVED, STOP_NEW_FAILURE_CLASS, STOP_STRUCTURAL_REDESIGN
```

A API aditiva de B-HO-02 **não** é `STOP_S1B_REQUIRES_S1A_SEMANTIC_CHANGE`: ela é aditiva e não muda a semântica existente da S1-A, que é congelada. Mas **exige requalificação focal da S1-A** (D-B-HANDOFF).

### 30.5 Final disposition

```yaml
disposition: S1B_FINAL_BOUNDARY_ADJUDICATION     # #301 5921263805 (sobre a emenda 5921013078); a disposição terminal é registrada no forge após a revisão do exact head, não neste arquivo
reviewed_head: b4a572a97465472d94165977edb05f720faf44eb     # independent review → S1B_FREEZE_CORRECTION_REQUIRED
correction_rounds: [1 (3dc2965), 2 (adjudication round)]
independent_review_of_b92a102: {material_findings: 0, authority_conflicts: 0, owner_conflicts: 0, obligation_domains: 11/11_CONFORMANT, S1B-REV-NIT-01: ACCEPTED_NON_BLOCKING_EDITORIAL}
pending_decisions: []
ArchitectureFreezeReady: false    # nova adjudicação humana exigida
ImplementationGrant: false
next_authorization: "revisão independente do successor + Codex; depois nova adjudicação humana de ArchitectureFreezeReady"
```

### 30.6 Correction round 1 (review independente de `b4a572a`)

Cada finding foi **reproduzido** antes de ser corrigido (`ReviewerObservation != FindingEstablished`). A evidência está em `experiments/p2c_corrections_results.json`: 13/13, estável em 3 execuções.

| Finding | Validação | Correção | Disposição |
|---|---|---|---|
| S1B-REV-01 Popen/fd closure | VALID (P2c REV01: `preexec` vê `[0..10]`, exec vê `[0,1,2]`) | B-CAP-01/04/05, TCB, claim budget, P2: premissa delegada, não prova direta; B como fallback | FIXED |
| S1B-REV-02 término assíncrono | VALID (T1: SIGTERM → 2 sobreviventes; T3 SIGKILL; T4 PDEATHSIG insuficiente) | domínios de término; handlers que só registram + wakeup fd; pidns-init (T5 = 0 sobreviventes); B-LIF-08/09; **D-B-LIFE-PIDNS ADOPT na rodada 2** | FIXED |
| S1B-REV-03 peek | VALID (ENOTSOCK; readline e read(4096) consomem o body) | `MSG_PEEK` removido; leitor exato por byte; buffers proibidos; discriminador `FIONREAD` | FIXED |
| S1B-REV-04 informação de falha | VALID ("primária preservada" perdia o close sob teardown) | `FailureOutcome` com 3 dimensões + matriz de 7 combinações | FIXED |
| S1B-REV-05 cópias da preimage | PARTIAL (a contagem "2 → 3" misturava construtor e verificador) | regra B3 MUST/MUST_NOT; helpers registrados como não adequados como estão; recontagem | ACCEPTED_LIMITATION (+ contagem corrigida) |
| S1B-REV-06 safe.directory | VALID como limitação (a justificativa anterior era fraca) | adjudicação do mantenedor (D-B-SAFE-DIR-VALUE, rodada 2); truth-maker próprio da S1-B; fato P2c REV06 como suporte | ACCEPTED_LIMITATION |
| S1B-REV-07 não-filho | VALID (`not_child_skipped == 0` em todas as execuções) | `ArchitectureMechanismSpecified != ImplementationBranchQualified`; witness obrigatório para B2 | ACCEPTED_LIMITATION |
| S1B-REV-08 lazy fetch | INVALID como defeito (config sem remote/promisor/partialclone + `protocol.allow=never` + `missing` local) | nenhuma; a claim segue delimitada ao caminho de aquisição de objetos modelado, sem afirmar "nenhuma interação externa possível" | FALSE_POSITIVE |

**Novelty lane (sobre o delta corretivo):**
- Handlers que **levantam** criariam janelas de exceção assíncrona (família N3 da S1-A). Por isso o freeze exige handlers que só registram, com wakeup fd. É a mesma família, não uma classe nova.
- A pré-condição de pidns muda: a vista do procfs (B-RC-02 verifica `/proc/self`), a ideia de "dedicado" (o init também reapeia órfãos, o que é compatível) e a semântica de SIGTERM vindo do ancestral (ignorado sem handler, T6).
- Nenhuma classe nova de falha material foi identificada. **`STOP_NEW_FAILURE_CLASS` não disparado.**

### 30.7 Correction round 2 (adjudicação do mantenedor [5915365542](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5915365542))

| Correção | Conteúdo |
|---|---|
| D-B-CAP-TCB | REV-01 registrado como `AUTHORIZED_CONTRACTION`: proposição funcional inalterada (`GitGetsOnlyAdmittedFDs`); garantia contraída para a premissa TCB do subprocess do CPython 3.11 (§8, §29) |
| D-B-LIFE-PIDNS | ADOPT: `pidns_identity` entra na expectativa (B-RC-01); identidade de pidns e vínculo do procfs verificados como controles distintos (B-RC-06); pidns, mntns e procfs privados estabelecidos pelo #350 antes da queda de privilégios (§4, §22); refinamento de U3 na #350 (5915369380) |
| D-B-SAFE-DIR-VALUE | ADOPT: "adjudicação", não "interpretação"; o comportamento do Git 2.43 é só evidência de suporte (§10, §29) |
| Evidência de B-LIF-09 | P2c T1/T2/T6/T7 recalibrados: estabelecem insuficiência do default, interceptabilidade e possibilidade de limpeza; **não** qualificam o mecanismo final (handler sem raise + wakeup fd), cuja demonstração fica em B2 (§14, §16, §17) |

**Novelty lane (sobre o delta da rodada 2):**
- **Nova autoridade?** Não. `pidns_identity` é fornecida pelo #350, que já é dono de `reader_context_establishment` e agora o refinou explicitamente. A S1-B só observa e compara.
- **Circularidade?** Não. A cadeia é `#350 estabelece → expectativa explícita → observação e comparação pela S1-B`. B-RC-01 proíbe derivar a expectativa do reader, e B-RC-06 separa a identidade (fornecida vs. observada) do vínculo do procfs.
- **Conflito de dono?** Não. Os itens de pidns, mntns e procfs pertencem ao #350 (5915369380), e a S1-B fica só com a verificação.
- **`STOP_NEW_FAILURE_CLASS`: não disparado.**

### 30.8 Correction round 3 (review post-Ready 5370887198 em `4343dba`; [5918385379](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5918385379))

> As revisões limpas anteriores e a ratificação 5917390110 foram **superadas por novos countermodels post-Ready**.

| Finding | Classe | Reprodução | Correção | Evidência discriminante |
|---|---|---|---|---|
| 4148411697 wakeup fora do contrato fechado de fds | MATERIAL | a allowlist antiga recusa uma implementação conforme (P2d CM-W1W2) | papéis tipados `signal_wakeup_read/write` (não bloqueantes, CLOEXEC, não herdados); lifecycle `set_wakeup_fd(-1)` → close; wakeup pré-existente recusado | P2d W-POS, CM-W3..W6 |
| 4148411705 handshake síncrono do `Popen` | MATERIAL | A: controle em 3,002 s contra deadline de 1 s | **D-B-SPAWN-MECHANISM-R3 = B**: deadline antes do fork, pidfd logo após o fork, canal de erro sob deadline | P2d S-B2 1,002 s; ablação 3,0 s; P2 (B) 44/44 ×3 |
| 4148411723 máscara de sinais sem restrição | MATERIAL | com o sinal bloqueado na entrada, o handler nunca roda | **B-LIF-10**: `pthread_sigmask(SIG_UNBLOCK)` + leitura de volta | P2d CM-R3-04..06 (normalizado vs ablação) |
| 4148411691 census REV01 não discriminante | EVIDENCE_GAP | filtro `fd <= 2` | census estável sem filtro + mutante `pass_fds=(extra,)` | P2c REV01 14/14 ×3 (mutante RED) |
| 4148411715 F3 afirma ausência de filhos | NON_MATERIAL_OVERCLAIM | docstring sem observação | claim estreitada: F3 mede só `<oid> missing`; lazy fetch irrelevante continua sustentado por config do produtor + argv/env + caminho modelado | — |

**TCB:** o caminho primário deixa de depender da premissa `close_fds` do `_posixsubprocess`. O bootstrap do filho inspeciona o conjunto pré-exec. As premissas novas são o `fork`, `dup2`, `setrlimit` e `execve` do CPython 3.11 sobre o kernel (§10).

**Novelty lane:**
- **Novo dono?** Não. B fecha a unidade sem supervisão externa (sem `STOP_NEW_AUTHORITY_OWNER_REQUIRED`).
- **Nova autoridade?** Não. A máscara é estado local da S1-B.
- **Novo gap de lifecycle?** Estado D durante um `execve` bloqueado no kernel continua declarado (não morto por SIGKILL).
- **Nova capability?** Sim, o canal de wakeup e o canal de erro, agora tipados e com dono.
- **Novo TCB?** Sim, o bootstrap do filho, declarado.
- `STOP_NEW_FAILURE_CLASS` não disparado.

**Resultado da round 3 (append-only):** `NOT_CONVERGED` em `a727c7e`. Revisão independente e Codex 5371340717 acharam countermodels novos contra B; round 3b autorizada em [5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387).

### 30.9 Correction round 3b ([5919204387](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5919204387); predecessor `a727c7e`, round 3 NOT_CONVERGED)

Base realinhada linearmente ao master atual `d3f5946`. Dois drifts, ambos ortogonais:
- **#365:** docs-only, só `campaign/**`;
- **#366:** fix V1 em `semantic_chunker.py` e testes V1, sem overlap de arquivo e fora do fecho de imports que a evidência S1-B carrega.

O conteúdo S1-B ficou byte-idêntico após o rebase. A evidência de `a727c7e` é histórica, e toda a evidência abaixo foi refeita neste tree.

**Ledger** (os findings da revisão independente sem thread entram aqui):

| Finding | Fonte | Família | Correção | Evidência discriminante |
|---|---|---|---|---|
| 4148788381 = N1 | Codex + revisão independente | SIGNAL_STATE_NORMALIZATION | B-LIF-10 reescrito: handlers e wakeup **antes** do desbloqueio; inspeção imediata do wakeup | R3B-S1..S3, R3B-S1-pidns |
| N2 | revisão independente | SIGNAL_STATE_NORMALIZATION | B-LIF-12: seção crítica do fork + reset no filho; §23 passa a **exigir** o reset | R3B-S4, R3B-S4b |
| N3 | revisão independente; dono adjudicado S1-B | SIGNAL_STATE_NORMALIZATION | B-LIF-11 + `reader_sigchld_state_not_normalized` | R3B-C1..C3, mutante só-handler |
| 4148788387 | Codex | BOUNDED_HANDSHAKE_TEARDOWN | nenhuma espera bloqueante após o deadline; a posse passa ao teardown com `WNOHANG` | R3B-H2 (test double) |
| N4 | revisão independente | BOUNDED_HANDSHAKE_TEARDOWN | o `select` do HANDSHAKE inclui `signal_wakeup_read` | R3B-S5 |
| N5 | revisão independente | BOUNDED_HANDSHAKE_TEARDOWN | §20 reescrita como máquina **única**, com verificador mecânico | `p2d_state_machine_check` L1–L10, M1–M9 |
| N6 | revisão independente | BOUNDED_HANDSHAKE_TEARDOWN | bootstrap terminal (`execve` ou `os._exit`) congelado | R3B-U1 |
| 4148788393 | Codex | EVIDENCE_TEXT_FIDELITY | §27 B2: "spawn A" → spawn B | census estático abaixo |
| 4148788399 | Codex | EVIDENCE_TEXT_FIDELITY | sinal sincronizado **dentro** do teardown, e sinal duplo | R3B-S6, R3B-S7 |
| NIT números | revisão independente | EVIDENCE_TEXT_FIDELITY | números citados do JSON admitido | — |
| NIT IDs | revisão independente | EVIDENCE_TEXT_FIDELITY | CM-R3-01..03 → CM-W; CM-R3-12 → ID real do P2c | — |
| NIT canal de erro | revisão independente | EVIDENCE_TEXT_FIDELITY | o pai fecha a cópia da escrita antes do `select` (§8, §21) | — |
| NIT F3 | revisão independente | EVIDENCE_TEXT_FIDELITY | docstring sem "answered locally" | — |
| NIT CM-W3 composto | revisão independente | EVIDENCE_TEXT_FIDELITY | barreiras isoladas (CM-W3a/b/d) e todas removidas (CM-W3c) | — |
| NIT cleanup do wakeup | revisão independente | EVIDENCE_TEXT_FIDELITY | cleanup em `finally` interno, que roda mesmo se o teardown levantar | — |

**Contrato de evidência:** cada família R3B tem um registro em `p2d_spawn_handshake_results.json` → `contracts`, com os campos `injection_confirmed`, `positive_control`, `negative_control`, `ablation_or_mutant`, `observed_discriminator` e `claim_boundary`. Os 18 contratos (iteração 2) têm `injection_confirmed: true`. Nos de S4 e S4b, a confirmação é a morte do filho por SIGTERM, e não o pidfd. Injeção não confirmada é FAIL, nunca PASS.

**Census estático de consistência** (item 28): termos `Popen`, `mechanism A`/`mecanismo A`, `spawn A`, `A selecionado`, `CPythonSubprocessInheritancePremise`, máscara, `SIGCHLD`, `set_wakeup_fd`, `HANDSHAKE`, `TEARDOWN` e `waitid(`.
- Cada ocorrência de A, `Popen` ou da premissa é de um destes tipos:
  - **histórico marcado** (§3, §10, §14 limitações, §29 D-B-SPAWN-MECHANISM, §30.1, §30.2, §30.6, §30.8);
  - **rejeição** (§14 alternativas);
  - **proibição** (§23);
  - **evidência comparativa** (§13).
- A ocorrência ambígua da §11 foi qualificada como histórica.
- Não há `waitid` bloqueante normativo: §13, §14 e §20 exigem `WNOHANG`.
- **`stale_current = 0`.**

**Autoridade e novelty lane:**

```yaml
new_owner: none
new_authority: none
SIGCHLD_normalization: {owner: S1-B}
signal_mask_normalization: {owner: S1-B}
reader_context_provenance: {owner: "#350"}
pidns_mntns_procfs_establishment: {owner: "#350"}
"#350_commented_this_round": false
spawn_B_core_property: not_refuted          # D-B-SPAWN-MECHANISM-R3 kept, reopen: false
stops_triggered: none                       # incl. STOP_CHILD_BOOTSTRAP_UNWIND_UNQUALIFIED (R3B-U1 discriminates)
new_TCB_premises: "signal ops in the child bootstrap; kernel pending/exec/pidns-init/SIGCHLD semantics (§10)"
declared_residuals: [real_D_state_kill (test double only), pre_exec_child_death_vs_immediate_post_exec_exit indistinguishable (transport_failed), exact_V1_domain_witnesses (root, B2), controlled_signal_before_reader_bootstrap_step_1 (review F5; closing it would be a new #350 obligation, not created)]
```

**Iteração 2 da round 3b.** A revisão independente e o Codex 5371956614 em `8cad5d3` acharam defeitos nas famílias já autorizadas. Nenhum é quebra do mecanismo B, e nenhum trouxe dono ou autoridade nova.

| Finding | Fonte | Família | Correção | Evidência discriminante |
|---|---|---|---|---|
| F1: o verificador da §20 não impunha a própria lei | revisão independente | BOUNDED_HANDSHAKE_TEARDOWN (N5) | leis L8 (nenhuma aresta de estado do reader para estado do filho), L9 (só `OUTCOME` é terminal) e L10 (timeout só pela aresta sem espera bloqueante) | mutantes M7 (`HANDSHAKE → CHILD_EXIT`), M8 (estado `ABANDONED` do tipo filho) e M9 (rótulo de espera bloqueante) mortos; os dois bypasses do revisor agora ficam RED |
| F2: contrato de S4/S4b passava sem sinal | revisão independente | EVIDENCE_TEXT_FIDELITY | `injection_confirmed` e a linha positiva exigem o filho morto por SIGTERM (`CLD_KILLED`/15), observado por `waitid(WNOWAIT)` antes do teardown | R3B-S4, R3B-S4b |
| F3 = Codex 4149311518: teardown fora do envelope | revisão independente + Codex | BOUNDED_HANDSHAKE_TEARDOWN | lei reformulada (`UnitEnvelope = WorkDeadline + TeardownReserve`, fixados antes do `fork`); o teardown usa o que resta do envelope | R3B-H2: 2,503 s num envelope de 2,5 s; R3B-O1: excesso de 0,003 s |
| F4: SIGPIPE/SIGXFSZ ignorados herdados pelo Git | revisão independente (novelty) | SIGNAL_STATE_NORMALIZATION ("no mínimo" do item 9) | o filho restaura `SIG_DFL` para toda disposição `SIG_IGN` | R3B-G1: `SigIgn` 0 vs 0x1001000 (alvo C `grep`) |
| F5: sinal antes do passo 1 do bootstrap | revisão independente (plausível) | SIGNAL_STATE_NORMALIZATION | **resíduo declarado** (B-LIF-10, §28 `may_not_claim`); fechá-lo exigiria obrigação nova da #350, não criada | — |
| Codex 4149311510 = review N-b: máscara não restaurada se o `fork()` falhar | Codex + revisão independente | SIGNAL_STATE_NORMALIZATION | `fork` sob guarda; aresta `fork_error` na §20; o sinal em `READY_TO_FORK`/`FORK_CRITICAL` é observado pelo HANDSHAKE | R3B-F1 (`[]` vs `[1,2,15]`); R3B-S8 |
| Codex 4149311525: pidfd do dono não fechado | Codex | BOUNDED_HANDSHAKE_TEARDOWN | o teardown fecha o pidfd do dono exatamente uma vez; census pós-teardown | S-B1, W-POS: `pidfds_open_after = 0`, `owner_pidfd_closed` |
| Codex 4149311538: outcome não derivado do teardown | Codex | BOUNDED_HANDSHAKE_TEARDOWN | outcome final decidido depois do teardown; primária preservada | R3B-O1 (ablação → `terminated`) |
| N-a: "EOF → exec confirmado" | revisão independente | EVIDENCE_TEXT_FIDELITY | "exec presumido"; aresta `exec_presumed_on_eof` | — |
| N-c: número no README | revisão independente | EVIDENCE_TEXT_FIDELITY | corrigido | — |

### 30.10 Final convergence round 3c (adjudicação do mantenedor em `b454723`: 3b NOT_CONVERGED → FINAL_CONVERGENCE_ROUND_3C)

**Lei de convergência:** `ArchitectureMechanismSpecified != ImplementationQualified` e `ExperimentSupportsMechanism != ImplementationQualification`. Todo finding é classificado primeiro.

| Classe | Definição | Disposição |
|---|---|---|
| `ARCHITECTURE_BLOCKER` | o mecanismo é falso; obrigações normativas se contradizem; falta dono ou autoridade; a propriedade é impossível no domínio declarado | `MUST_CLOSE_BEFORE_FREEZE` |
| `QUALIFICATION_GAP` | experimento pouco discriminante; sincronização fraca; mutação ou corpus incompletos; o spike descartável não exercita o ramo exato da implementação | `DEFER_TO_OWNING_B_SLICE_WITH_EXPLICIT_OBLIGATION` |

**Ledger de classificação** (revisão independente e Codex 5372183232 em `b454723`):

| Finding | Classe | Disposição |
|---|---|---|
| review F3 = Codex 4149510404 (máscara herdada chega ao Git) | ARCHITECTURE_BLOCKER (M1) | **fechado**: estado de sinais do exec construído; R3C-M1a/b |
| Codex 4149510376 (cache do `getsignal` ≠ kernel) | ARCHITECTURE_BLOCKER (M1) | **fechado**: `sigaction` no kernel + leitura de volta; R3C-M1c |
| review F2 (um candidato preso priva os irmãos) | ARCHITECTURE_BLOCKER (M2) | **fechado**: teardown em duas fases; R3C-M2, R3C-M2b |
| Codex 4149510393 (falha do `fork` perde o cancelamento) | ARCHITECTURE_BLOCKER (M3) | **fechado**: dimensão `termination_request`; R3C-M3 |
| Codex 4149510370 (pidfd do dono fora de guarda externa) | ARCHITECTURE_BLOCKER (M4) | **fechado**: `finally` externo; 5 famílias de falha + pipes do protocolo; R3C-M4 |
| review N4 (teardown com orçamento 0 não sinaliza) | ARCHITECTURE_BLOCKER menor, absorvido por M2 | **fechado**: pelo menos uma passada de atribuição e sinal |
| review F1 (o verificador da §20 aceita arestas contraditórias) | QUALIFICATION_GAP (Q1) | claim contraída (lint estrutural ≠ prova de completude); B2 testa todo caminho terminal |
| review F4 (sinal duplo sem duas entregas observadas) | QUALIFICATION_GAP (Q2) | claim contraída; obrigação B2 |
| Codex 4149510384 (S4 sem sincronização com o ponto de reset) | QUALIFICATION_GAP (Q3) | S4/S4b rebaixados a suporte preliminar; obrigação B2 |
| Codex 4149510365 (`read_report(…, 5.0)` ignora o deadline de trabalho) | QUALIFICATION_GAP (Q4) | o spike passou a usar o restante do deadline (mudança pequena, para manter a evidência honesta); a qualificação é obrigação B2/B3 |
| review N1 ("seis mutantes (M1–M9)"), N2 (histórico do README reescrito), N3 (`injection_confirmed` fraco em G1/H1) | EVIDENCE_TEXT_FIDELITY | corrigidos (texto; histórico restaurado; confirmações de G1/H1 não tautológicas) |

**`B2_MANDATORY_QUALIFICATION_GAPS`** (estarem pendentes significa `ImplementationNotYetQualified`, **não** que a arquitetura é incongelável):

```yaml
B2_MANDATORY_QUALIFICATION_GAPS:
  - exact PID namespace positive in the V1/root domain (B-RC-06, B-LIF-08)
  - ECHILD non-child candidate branch of the childness proof (S1B-REV-07)
  - cross-principal immutability positive (B-IMM, STOP_POSITIVE_CONTROL_UNAVAILABLE)
  - setuid / file-capability privilege negatives (B-PRV-04)
  - two independently OBSERVED controlled-signal deliveries during teardown:
      "send #1 → observe delivery/wakeup #1 → teardown active → send #2 → observe delivery/wakeup #2 → teardown completes or typed failure (two kill() calls alone are insufficient)"
  - exact child post-reset signal witness:
      "the child emits/causes an observable post-reset synchronization event → only then inject → demonstrate the canonical child signal state"
  - post-exec stall under the absolute unit deadline (B2/B3):
      "exec succeeds, the child stalls before the first protocol line → the unit ends within the same absolute envelope; every blocking/read wait uses remaining(absolute_deadline_at)"
  - teardown fairness with one stuck and one killable child, in the implementation branch
  - kernel-level signal disposition and mask verification in the implementation branch
  - every terminal edge of the §20 state machine exercised
```

**Contração de claims por experimento** (item 12):

| Experimento | Estabelece | Não estabelece | Dono da qualificação futura |
|---|---|---|---|
| P2 (`harness.py`) | as obrigações TF5 do mecanismo A (histórico) e B no spike | qualificação do ramo de implementação | B2 |
| P2b (`git_facts.py`) | gramática do header, paridade da preimage sha1/sha256, `<oid> missing` com rc 0 | onde é respondido; ausência de descendentes | B3 |
| P2c (`p2c_corrections.py`) | REV01 (visão de fds + mutante), REV02 (domínios de término), REV03 (`MSG_PEEK`, consumo), REV06 (`safe.directory`) | o handler final (P2c levanta) | B2 |
| P2d (`p2d_spawn_handshake.py`) | cada propriedade de mecanismo das rounds 3/3b/3c é discriminada pela sua ablação ou mutante neste spike | qualificação dos ramos B1/B2/B3; estado D real (só doubles da camada de reap) | B2 (lifecycle/sinais/teardown), B3 (deadline de transporte) |
| verificador da §20 | as leis sintáticas enumeradas L1–L10, com M1–M9 | completude semântica de todas as arestas possíveis | B2 |
| spike de sinal duplo (R3B-S7) | o cenário atual (dois `kill`, entregas observadas quando não coalescem) | duas entregas **observadas independentemente** durante o teardown | B2 |
| spike de reset do filho (R3B-S4/S4b) | entrega e morte por `SIG_DFL`; a misatribuição sem reset | qual lado do ponto de reset recebeu o sinal | B2 |
| spike de deadline pós-exec (`read_report`) | que o spike usa o restante do deadline de trabalho | a regra global na implementação | B2/B3 |

O mesmo bloco de claims está em `p2d_spawn_handshake_results.json` → `claims`.

**Autoridade:** `new_owner: none`, `new_authority: none`; a #350 não foi comentada.

**Regra terminal da round 3c** (uma única iteração):
- revisão com `architecture_blockers: 0` → `S1B_ARCHITECTURE_FREEZE_READY_FOR_MAINTAINER_ADJUDICATION`, desde que todo `QUALIFICATION_GAP` tenha dono B1/B2/B3 e nenhum experimento o exagere;
- `architecture_blockers > 0` → `STOP_STRUCTURAL_REDESIGN`.

### 30.11 Final bounded freeze amendment ([5921013078](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5921013078); predecessor `0053d77`)

O `STOP_STRUCTURAL_REDESIGN` de `0053d77` foi adjudicado pelo mantenedor: `procedural_stop: valid`, `structural_redesign: required: false`. Os dois `ARCHITECTURE_BLOCKER` são contradições normativas locais. Nenhum altera o mecanismo B, a divisão de autoridade e dono, a topologia da máquina de estados ou o slicing B1/B2/B3.

Base realinhada linearmente a `2941b55` (#351, ortogonal); o conteúdo S1-B ficou byte-idêntico após o rebase.

| Blocker | Disposição | Emenda | Evidência |
|---|---|---|---|
| AB-1: reservados NPTL 32/33 | BOUNDED_NORMATIVE_AMENDMENT | a afirmação universal "todo sinal capturável → `SIG_DFL`" foi removida. Duas categorias: (a) conjunto endereçável pela libc → `SIG_DFL` e máscara vazia; (b) reservados 32/33 → bits `SigIgn`/`SigBlk` no kernel verificados limpos, senão recusa antes do exec (B-LIF-12 passo 7) | P2e AB1-POS, AB1-CM1..CM4 (recusa) vs mutante só-libc (aceita); B2 qualifica |
| AB-2 = Codex 4149910902: término observado no teardown | BOUNDED_NORMATIVE_AMENDMENT | `FinalOutcomeIsDerivedAfterTeardown`; definição de `COMPLETED`; sinal no teardown → `FailureOutcome` com `unit_terminated_by_signal`; precedência preservada; "3 dimensões / 7 combinações" trocado pelos invariantes I1–I4; §20 esclarecida sem mudar a topologia | P2e AB2-POS (x2) vs mutante que captura antes do teardown (`COMPLETED`); B2 qualifica |

**`B2_MANDATORY_QUALIFICATION_GAPS`** (transferência formal; complementa a lista da §30.10, que continua valendo):

```yaml
B2_MANDATORY_QUALIFICATION_GAPS_ADDENDUM:
  state_machine:
    - exercise every terminal edge in the implementation
    - the structural checker is a lint/mutation discriminator, not a completeness proof
  double_signal:
    - two independently observed controlled-signal deliveries during teardown
  child_reset_point:
    - synchronized post-reset injection point
  post_exec_deadline:
    - every wait/read uses the remaining absolute deadline (B2/B3)
  guard_coverage:
    - injected failures at every capability/ownership phase, including the window between fork() and pidfd/Handle ownership (Codex 4149910886)
  capability_close_confirmation:
    - ownership released only after the close outcome is recorded; fault injected at the real close boundary (Codex 4149910893)
  experiment_source_identity:
    - qualification artifacts bound to the exact source/digest where required (Codex 4149910908)
  teardown_budget_floor:
    - witness of at least one attribution+signal pass on an exhausted envelope (review QG-1)
  reserved_signal_state:
    - production bootstrap refuses unexpected NPTL-reserved 32/33 ignored/blocked state before exec (AB-1)
  outcome_after_teardown:
    - production outcome derivation after final teardown observations; signal during teardown after normal completion (AB-2)
  claims_metadata:
    - per-experiment claims block present for every qualification artifact (review QG-2)
```

**Status dos experimentos:** P2, P2b, P2c, P2d e P2e têm o papel de `design_evidence`, `countermodel_discovery` e `causal_support`, e **não** de `implementation_qualification` (`ExperimentalEvidence != ImplementationQualification`). Eles não são apresentados como qualificação coletiva de B2. Os spikes **não** foram endurecidos nesta emenda (item 17); as lacunas acima foram contraídas e atribuídas.

**Lei:** `QualificationGap != ArchitectureBlocker`, dado um mecanismo totalmente especificado, dono e autoridade existentes, nenhuma contradição normativa restante e obrigação de qualificação explícita.

**Regra terminal:** um único ciclo de revisão.
- `architecture_blockers: 0`, lacunas explícitas e atribuídas, sem conflito de autoridade ou dono, sem contradição normativa, CI GREEN e Codex sem blocker → `S1B_ARCHITECTURE_FREEZE_READY_FOR_MAINTAINER_ADJUDICATION`;
- `architecture_blockers > 0` → `S1B_ARCHITECTURE_REDESIGN_REQUIRED`.

### 30.12 Final boundary adjudication ([5921263805](https://github.com/mglpsw/aiops-orchestrator/issues/301#issuecomment-5921263805); predecessor `40f8060`)

Prior: `S1B_ARCHITECTURE_REDESIGN_REQUIRED` (Codex 5372868625). Adjudicação: `procedural_stop_valid: true`, `structural_redesign_required: false`. Isto é uma adjudicação de fronteira, não uma nova campanha de correção.

| Codex | Disposição | Fronteira congelada |
|---|---|---|
| 4150092312 | `TCB_BOUNDARY_UNDER_SPECIFIED` | `S1B_V1_TCB` (Linux, glibc/NPTL, CPython 3.11, bootstrap fork/exec da S1-B) e `NPTL_RESERVED_SIGNALS` (`libc_internal`, confiável, sem canonicalização, sem verificação de autoria, sem exigir `SigCgt` limpo). A afirmação universal foi estreitada para "todo sinal capturável **controlado pela aplicação** no domínio de runtime declarado". `NoChildCanRunInheritedApplicationControlledReaderHandlerBeforeReset`. Não-claim: adulteração nativa no mesmo processo é comprometimento do TCB |
| 4150092315 | `OUTCOME_LINEARIZATION_UNDER_SPECIFIED` | `FINALIZATION_BARRIER`: bloqueio atômico de CONTROLLED depois do teardown; sequência barreira → drenagem → flags → derivação → handoff; `DeliveredBeforeFinalizationBarrier → belongs_to_current_unit`, `ArrivesAfterFinalizationBarrier → outside_current_unit`; CONTROLLED bloqueado até o handoff (V1: até a saída do reader; reuso não assumido); `COMPLETED` redefinido pela barreira; §20 esclarecida pela sub-fronteira na aresta `TEARDOWN → OUTCOME`, sem mudar a topologia |

**Witness focal:** P2e AB-2 (barreira), 13/13 em 3 execuções:
- entregue antes da barreira → `FailureOutcome`;
- com teardown incompleto → `unit_teardown_incomplete`;
- mutante que deriva antes da barreira → `COMPLETED`;
- sinal só depois da barreira → fora da unidade.

Para a fronteira de TCB não há tentativa de provar autoria. O runtime qualificador observado é glibc 2.39/NPTL.

`new_owner: none`, `new_authority: none`; a #350 não foi alterada.

```yaml
B2_MANDATORY_QUALIFICATION_GAPS_ADDENDUM_2:   # somado às listas da §30.10 e da §30.11, que continuam valendo
  finalization_barrier_runtime_race_witness:
    - production reader: signal delivered immediately before the barrier is captured by the post-barrier drain; one pending only after the barrier is outside the unit; CONTROLLED stays blocked until handoff/exit
  declared_libc_NPTL_runtime_domain_check:
    - confirm the qualifying libc/NPTL domain
    - confirm the application signal-state propositions (canonical dispositions, empty application mask)
    - do not claim reserved-signal handler authorship
```

**Regra terminal:**
- `architecture_blockers`, `authority_conflicts`, `owner_conflicts` e `normative_contradictions` = 0; fronteira de TCB coerente; `FINALIZATION_BARRIER` coerente com a corrida de outcome definida; lacunas explícitas e atribuídas; CI GREEN; Codex sem blocker → `S1B_ARCHITECTURE_FREEZE_READY_FOR_MAINTAINER_ADJUDICATION`;
- um blocker que falsifique genuinamente o mecanismo B ou estas duas fronteiras → `S1B_ARCHITECTURE_REDESIGN_REQUIRED`.
