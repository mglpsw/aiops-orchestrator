# #301-S1-B: Architecture Freeze, "safe reader context"

```yaml
status: ARCHITECTURE_FREEZE_DRAFT      # docs-only; aguarda revisão independente e adjudicação do exact head
ArchitectureFreezeReady: false         # passa a true só por adjudicação do mantenedor sobre o exact head revisado
ImplementationGrant: false             # ArchitectureFreezeReady != ImplementationGrant
implementation: NOT_STARTED            # B1, B2, B3 não iniciadas
owner_issue: "#301"                    # Refs #301; esta PR não fecha nenhuma issue
```

---

## 1. Live baseline

```yaml
repository: mglpsw/aiops-orchestrator
base:
  master_sha: ab92e89f096b391bc50759afa3d0f6050881633a
  master_tree: a811df6344ab5ba450b3a1b5fd12cd45d0477552
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
  "#46 reconciliada (2026-09-30)": "S1-A INTEGRATED; S1-B próxima, só planejamento; C4 incompleto; G5 não atingido"
state:
  S1_A: INTEGRATED
  S1_B: {architecture: FREEZE_DRAFT, implementation: NOT_STARTED}
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
| **#350 (U3)** | composição operacional do launcher; estabelecimento do `AuthorizedReaderExecutionContext`; transição uid/gid; proveniência da expectativa userns/mntns; proveniência do handoff de fd; proveniência do toolchain Git selecionado pelo host |
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
```

## 4. Applicability

```text
Given   AuthorizedReaderExecutionContext established by #350 (U3)
        ∧ ReaderContextExpectationV2 supplied explicitly by the host
        ∧ CompletePublicationV2.snapshot : PublishedSnapshotV2 (S1-A)
        ∧ reader process dedicated (single-thread, no other children) and created by #350
        ∧ domain(§24)
S1-B ⊢  descriptor-bound, privilege-non-escalating, contained Git transport whose delivered
        objects are content-address-authenticated, with lifecycle owned from spawn,
        or a typed refusal.
```

- **Domínio V1:** Linux com pidfd completo (`pidfd_open`, `pidfd_send_signal`, `waitid(P_PIDFD)`), x86_64/aarch64 (herdado da S1-A), CPython 3.11, filesystem no domínio da S1-A, Git ≥ piso por recurso (§24).
- **userns: `initial_user_namespace_only`**, com disposição `AUTHORIZED_CONTRACTION` (D-B-USERNS). Não é lei universal de segurança; ampliar exige scope reopen, desenho explícito e nova qualificação.
- **Fora do domínio → recusa tipada**, nunca operação degradada. Exemplos: kernel sem pidfd, userns não inicial, Git abaixo do piso.

## 5. Obligation domains

Exatamente 11 domínios. A matriz completa está em §16.

| Domínio | IDs | Propriedade central | Classe | Dono |
|---|---|---|---|---|
| HANDOFF | B-HO-01..04 | a capability da S1-A cruza a fronteira sem perda nem rebinding | MIXED | S1-B + API aditiva na S1-A |
| READER_CONTEXT | B-RC-01..05 | observação == expectativa explícita; procfs competente; userns inicial | LIVE | S1-B verifica; #350 estabelece |
| IMMUTABILITY | B-IMM-01..04 | `¬PrincipalCanMutate(reader, snapshot)` por inode | LIVE | S1-B |
| CAPABILITY_CLOSURE | B-CAP-01..05 | reader e Git recebem só fds de allowlist **tipada**; herança provada na fronteira pré-exec | LIVE | S1-B verifica; #350 fecha na origem |
| PRIVILEGE | B-PRV-01..04 | caps = 0; `PR_SET_NO_NEW_PRIVS` antes do exec (`S1_CTX_01`) | LIVE | S1-B |
| EXEC_CONFINEMENT | B-EXE-01..05 | Git relativo ao fd; env e args autorados; request só com OID completo | MIXED | S1-B |
| TRANSPORT | B-TRN-01..05 | header estrito em bytes; zero prefetch; admissão como type-state; framing exato; mesmos bytes | MIXED | S1-B |
| OBJECT_AUTH | B-AUTH-01..03 | preimage canônica Git == OID pedido | MIXED | S1-B; posição semântica é da S1-C |
| RESOURCE | B-RES-01..06 | deadlines; `RLIMIT_AS` pré-exec; loop único; stderr sem conteúdo; R4-3 Policy B | MIXED | S1-B mecanismo; valores da #320 |
| LIFECYCLE | B-LIF-01..07 | dono antes do filho; identidade pelo kernel; teardown em toda saída; zero sobreviventes ou falha tipada (`S1_LIFE_01`) | LIVE/MIXED | S1-B |
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

- **B-RC-01.** `ReaderContextExpectationV2` é **explícita e fornecida pelo host** (#350). Não existe `from_current_process()` nem default derivado de `/proc`, porque isso seria tautologia.
- **B-RC-02. Procfs competente.** O truth-maker:
  - abrir um descriptor de procfs;
  - verificar `fstatfs(fd).f_type == PROC_SUPER_MAGIC`;
  - fazer as observações relativas a essa vista admitida.

  Onde houver syscall mais competente que parsing textual, ela vence: `getresuid`, `getresgid`, `getgroups`, `prctl(PR_GET_NO_NEW_PRIVS)`, `capget`. Não conseguir observar ou observar ambiguidade (por exemplo chave duplicada) → recusa.
- **B-RC-03. Comparação completa, antes de qualquer spawn:**
  - ruid, euid, suid e fsuid;
  - rgid, egid, sgid e fsgid;
  - grupos suplementares;
  - identidade do userns e do mntns;
  - conjuntos de capabilities;
  - `NoNewPrivs`.
- **B-RC-04. userns V1:** `initial_user_namespace_only`. O inode de `ns/user` é igual à constante do kernel para o userns inicial e à expectativa. O mntns é comparado à expectativa, porque não há constante de kernel equivalente.
- **B-RC-05.** `ObservedContextMatchesExpectation != ExpectationIsAuthorized`. A proveniência da expectativa é da #350.

## 8. Immutability + inherited FD capability closure

**IMMUTABILITY** (`ModeSaysReadOnly != NoWritableCapability`):

- **B-IMM-01.** O reader não é dono dos inodes materiais. A verificação é um walk por fd com `O_NOFOLLOW` a partir do fd admitido, limitado e cruzado com `receipt.files_copied`.
- **B-IMM-02.** O reader não tem autoridade de escrita pelos bits de modo aplicáveis (owner, grupo se o gid estiver em seus grupos, other) nem por ACL. A presença de xattr de ACL POSIX → recusa conservadora (over-rejection declarada). Onde houver verdade derivada do kernel, preferi-la.
- **B-IMM-03.** Só arquivo regular e diretório. Special files e symlinks → recusa.
- **B-IMM-04.** `ReadOnlyView != NoWritableAlias`. TF1 (alias de mount) é falsificador obrigatório. O truth-maker é a identidade por inode, nunca `access(path)`.

**CAPABILITY_CLOSURE:**

- **B-CAP-01.** O reader começa com um conjunto fechado de fds.
- **B-CAP-02.** Nenhum fd herdado dá escrita ou outra autoridade sobre o snapshot, o storage, a policy do host ou qualquer outro recurso que carregue autoridade.
- **B-CAP-03.** O Git herda só `{0, 1, 2}`: o request, a saída e o stderr do protocolo.
- **B-CAP-04. Allowlist tipada.** Verificada com `fstat`, `fcntl(F_GETFL)` (modo de acesso e `O_PATH`) e `fcntl(F_GETFD)` (`CLOEXEC`):

  ```yaml
  AllowedFd: {role, kernel_identity, object_type, access_mode, cloexec, inherited_by_reader, inherited_by_git}
  ```

- **B-CAP-05. A herança é provada na fronteira pré-exec.**
  - O mecanismo selecionado (A, §14) é `Popen` com `close_fds=True`, `pass_fds=()` e `preexec_fn` limitado a `setrlimit`.
  - A prova é o census tipado do reader tomado **imediatamente antes do spawn**: todo fd > 2 com `FD_CLOEXEC`, mais o caminho pré-exec fixo (o `_posixsubprocess` do CPython faz `dup2` para 0–2 e depois `close_fds`, que é premissa TCB, §10).
  - `/proc/<git-pid>/fd` pós-exec é **só corroboração** (`PostExecFdObservation != InheritanceProof`).
  - **Achado P2:** `subprocess.DEVNULL` abre `/dev/null` com **O_RDWR**. Um papel somente leitura exige um fd `O_RDONLY` explícito.

| fd (reader) | role | object_type | access | cloexec | inherited_by_git |
|---|---|---|---|---|---|
| 0 | stdin do reader (launcher) | chr/fifo | r | — | não (substituído) |
| 1, 2 | canais de resultado e diagnóstico | fifo | w | — | não (substituídos) |
| snapshot (owned dup) | snapshot_dir | dir | r (nunca `O_PATH`) | **sim** | não (o Git usa o cwd do `fchdir`) |
| procfs | procfs_view | dir | r | sim | não |
| pipes do protocolo | request / response / stderr | fifo | w / r / r | sim (lado do reader) | só a outra ponta, como 0/1/2 |

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

- **D-B-SAFE-DIR (`explicit_minimal_command_scope`):**
  - o escopo é **uma invocação de comando** (`-c`), e configuração ambiente é proibida;
  - o valor é `*` porque um valor de path tornaria um pathname entrada de uma decisão de segurança (`path_becomes_authority: false`) sem acrescentar nada à verificação por inode (B-IMM), que é a propriedade forte.
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
    - CPython 3.11 + stdlib (inclui o caminho filho de _posixsubprocess: dup2 → preexec_fn → close_fds → exec)
    - libc e dynamic loader
    - executável Git selecionado pelo host e suas bibliotecas de runtime
    - semântica de filesystem no domínio admitido
  not_proved_by_S1B:
    - proveniência desses componentes (dona: #350)
    - resistência a comprometimento do host
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

**B-TRN-02. Zero prefetch.** Nenhum byte do body é consumido antes de `BodyAdmittedObjectV2` (`TRANSPORT_HEADER_BODY_PREFETCH`, Codex 4117693716). O freeze fixa a **propriedade**. O mecanismo (leitura byte a byte ou peek) é escolhido em B3 com discriminador, que é um contador de bytes consumidos no momento da admissão.

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

**B-AUTH-02.** O `kind` é um dos tipos Git admitidos e está criptograficamente amarrado ao OID pelo hash.
- A S1-B **não** estabelece a posição semântica na closure commit/tree, que é da S1-C.
- Um `expected_kind` explícito do chamador pode ser comparado como restrição; isso não deriva semântica de closure.

**B-AUTH-03.** `AuthenticatedGitObjectV2` só existe depois de framing válido, body completo, hash completo, OID correto e tipo correto.

Limite: `AuthenticatedGitObjectV2 != AuthenticatedCommit != AuthenticatedClosure != AuthenticatedStableSubject`.

## 13. Resource envelope

- **B-RES-01.** Deadline monotônico por objeto, do request ao LF final, vindo de policy explícita. **Sem default de produção.**
- **B-RES-02.** Deadline da unidade, do spawn ao teardown. P2 (TF5-D): um filho travado antes do protocolo gera `unit_deadline`, com teardown limpo.
- **B-RES-03.** `RLIMIT_AS` antes do exec, via `preexec_fn` que **só** chama `setrlimit` (mecanismo A). P2: o filho observa o limite como primeira ação; a ablação `prlimit` depois do spawn mostra `unlimited`.
- **B-RES-04.** Um único loop `select` cuida do progresso de stdin, stdout e stderr e dos deadlines, sem threads.
- **B-RES-05.** Drenar todo o stderr e **não publicar stderr bruto**. O máximo é `stderr_observation: {bytes_seen, truncated, digest: opcional}`, e o reason code não carrega conteúdo (`BoundedLeak != NoLeak`).
- **B-RES-06. R4-3, Policy B** (`ReportedCauseRequiresObservedTruthMaker`). `transport_envelope_exceeded` só quando a causa foi observada. Sem truth-maker competente → `transport_failed`.

A #320 mantém: valores de produção de deadline, memória agregada, cgroup, NPROC geral, NFS/FUSE e política numérica do R4-3. **Este freeze não fecha a #320.**

## 14. Lifecycle

**Propriedade**, não API:

```text
OwnerEstablishedBeforeChildExists ∧ KernelBoundProcessIdentity ∧ TeardownOnEveryExit
∧ ZeroAttributableSurvivorsOrTypedFailure
```

**Mecanismo selecionado no P2: A, `Popen` controlado**, dentro de um reader dedicado. Estas partes são obrigatórias, cada uma com o discriminador P2 que a sustenta:

| # | Elemento obrigatório | Discriminador P2 |
|---|---|---|
| 1 | Pré-condição dedicada: single-thread (`threading.active_count()==1` e uma única task em `/proc/self/task`), verificada; nenhum outro filho | — |
| 2 | Subreaper: `PR_SET_CHILD_SUBREAPER` antes do spawn, verificado por `PR_GET_CHILD_SUBREAPER == 1` | ablação `no_subreaper` + grandchild com `setsid` → sobrevivente |
| 3 | NNP, `fchdir`, census tipado no reader antes do spawn | §8, §9, §10 |
| 4 | Spawn: `Popen(argv_absoluto, stdin/stdout/stderr = pipes do protocolo, close_fds=True, pass_fds=(), env=allowlist, cwd=None, preexec_fn=setrlimit_only)` | — |
| 5 | Identidade: `pidfd_open` logo após o retorno. O handle `Popen` **não** é identidade nem dono. `wait`, `poll` e `communicate` do `Popen` são proibidos pelo census, porque reapeiam por PID nu | — |
| 6 | Dono = o reader, por **atribuição do kernel**: a cada rodada, varre `/proc/[pid]/stat` por `ppid == self` (inclui netos reparentados ao subreaper) | ablação `handle_only` → sobrevivente em TF5-B, TF5-C e TF5-F |
| 7 | Para cada candidato: `pidfd_open`; **prova de filiação** por `waitid(P_PIDFD, WEXITED\|WNOHANG\|WNOWAIT)`, porque um não-filho dá ECHILD e não é sinalizado; `pidfd_send_signal(SIGKILL)`; reap por `waitid(P_PIDFD)`, tudo limitado pelo deadline | — |
| 8 | O teardown roda num `finally` que cobre toda saída, inclusive `BaseException` | — |
| 9 | Fim: nenhum filho restante e `waitid(P_ALL, WNOHANG\|WNOWAIT)` → ECHILD. Caso contrário → `unit_teardown_incomplete` | — |

- **B-LIF-01..07** estão em §16.
- Um grandchild com `setsid` continua no domínio, porque o subreaper o reparenta ao reader.
- PID nu não é identidade. P2 (TF5-G), num userns+pidns privado com `ns_last_pid`: um PID foi **realmente** reusado; o pidfd stale recebeu ESRCH e o processo não relacionado sobreviveu. Na ablação `bare_pid`, o processo não relacionado foi morto.

**Alternativas rejeitadas:**
- **B (fork/exec explícito)** fechou exatamente as mesmas obrigações (44/44), mas reimplementa o caminho filho do `_posixsubprocess` (`dup2`, fechamento de fds, canal de erro, exec). A superfície é maior e sem obrigação adicional fechada. Fica como alternativa: se a revisão recusar a premissa TCB de B-CAP-05, B oferece um census **dentro do filho** na última fronteira pré-exec, também exercitado no P2.
- **C (launcher mínimo)** não é admissível sem necessidade demonstrada (`LargerMechanismRequiresDemonstratedNeed`), e nenhuma obrigação falhou em A ou B.

**Limitações:**
- Processo em estado D não pode ser morto; um sobrevivente após o deadline é declarado. A testemunha de estado D não é produzível sem privilégio → `gate_unavailable` no P2.
- A interface `children` do kernel é ausente no host observado, então a atribuição usa `ppid` + prova de filiação por pidfd. O custo é O(#processos) por rodada.
- O ponto de injeção TF5-B depende do CPython 3.11 (`subprocess._fork_exec`), e isso é só teste.

## 15. Outcome semantics

- Os reason codes formam um conjunto **fechado e versionado**, fixado em B1, B2 e B3. Os candidatos seguem a numeração da matriz:
  - admissão: `reader_snapshot_not_admitted`, `reader_snapshot_binding_mismatch`, `reader_snapshot_mutable_by_reader`, `reader_snapshot_special_entry`, `reader_object_format_mismatch`;
  - contexto: `reader_context_mismatch`, `reader_context_unobservable`, `reader_procfs_unverified`, `reader_userns_not_initial`, `no_new_privs_unavailable`, `reader_process_not_dedicated`, `reader_fd_capability_unexpected`;
  - lifecycle: `subreaper_required`, `pidfd_unavailable`, `transport_spawn_failed`, `unit_deadline`, `unit_teardown_incomplete`, `reader_descriptor_close_failed`;
  - transporte: `transport_request_invalid`, `transport_header_invalid`, `transport_header_oversize`, `transport_object_missing`, `transport_kind_mismatch`, `transport_truncated`, `transport_framing_invalid`, `transport_object_deadline`, `transport_body_not_admitted`, `transport_envelope_exceeded`, `transport_failed`, `object_hash_mismatch`;
  - toolchain: `toolchain_below_floor`.
- **Precedência** (D-B-PRECEDENCE): `unit_teardown_incomplete > reader_descriptor_close_failed > primary_failure`. A falha primária é **preservada** como diagnóstico estruturado (`FailurePrecedence != FailureInformationLoss`).
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
| B-RC-04 | userns inicial + expectativa; mntns == expectativa | D-B-USERNS / S1-B | reader | inode de `ns/user` vs constante do kernel | comparação | host sem userns | TF2 (`unshare -rU`) | mutante só uid numérico | B2 (`gate_unavailable` sem userns) | contração V1 | LIVE |
| B-RC-05 | observação ≠ autorização | CONTRACT L137 / #350 | — | — | claim budget | — | apresentar observação como autoridade | review | freeze | — | STATIC |
| B-IMM-01..04 | reader sem autoridade de mutação por inode; só reg/dir; TF1 | CONTRACT L155, L388 (`¬PrincipalCanMutate`) / S1-B | reader | walk por fd + `fstat`/xattr | admissão | **snapshot de outro principal** | mesmo dono, 0666, group-writable, ACL, dir gravável, FIFO, symlink, alias de mount TF1 | mutante `access(path)` | B2 (**exige positivo cross-principal**) | ACL: over-rejection | LIVE |
| B-CAP-01..05 | fds tipados; herança provada pré-exec | adjudicação D-B / S1-B | reader e Git | census tipado + `CLOEXEC` + `close_fds` | census pré-spawn | só a allowlist chega | TF5-H, TF5-I, TF5-J, TF5-J2 | ablação `no_fd_census` | **P2 PASS**; B2 | premissa TCB `_posixsubprocess` | LIVE |
| B-PRV-01..04 | caps = 0; NNP antes do exec | CONTRACT L1220–1227 / S1-B | reader e Git | `prctl` + read-back; status do filho | reader dedicado | filho com `NoNewPrivs: 1` | setuid, setgid, file caps, X1, EPERM em kill | **P2: ablação `no_nnp` → 0** | P2 (NNP); B2 (X1 com root) | NNP não bloqueia LSM, userns nem IPC | LIVE |
| B-EXE-01 | acesso ancorado no snapshot admitido | freeze S1-A §19 / S1-B | Git | `fchdir(fd)` + cwd do filho == inode | cwd herdado | cwd == snapshot | rebind de path + decoy | **P2: ablação `path_cwd` → decoy** | P2 PASS; B3 | Git não toca outro arquivo: não afirmado | MIXED |
| B-EXE-02..03 | env e args autorados; `safe.directory` por comando; lazy fetch irrelevante | D-B-SAFE-DIR, D-B-GIT-FLOOR / S1-B | Git | dict de env + argv + config do produtor | spawn | `cat-file` funciona | `LD_PRELOAD`, `GIT_ALTERNATE_*`, replace ref, config/hook | mutantes que removem flags; mutante que herda env | P2b; B3 | proveniência do binário é da #350 | MIXED |
| B-EXE-04 | request só com OID completo lowercase | #301 / S1-B | Git | regex de bytes | validação antes do write | OID válido | `HEAD:x`, `C^{tree}`, maiúscula, curto | mutante leniente | B3 | — | STATIC |
| B-TRN-01 | header estrito em bytes | CONTRACT L591 / S1-B | transporte | parser de bytes | máquina de estados | **P2b: 26 headers reais** | 9 do S0 + `+5`, `1_0`, `007`, tab, CRLF, NUL, partido, longo, tipo ou OID divergente, `missing`, `ambiguous` | mutante `int()`/`strip` | P2b; B3 | — | MIXED |
| B-TRN-02 | zero prefetch | Codex 4117693716 / S1-B | transporte | contador de bytes na admissão | leitura limitada | 0 bytes | fill de 64 KiB do S0 | mutante com prefetch | B3 | buffer do kernel não contado | LIVE |
| B-TRN-03 | admissão como type-state | adendo 5861631976 / S1-B | transporte | estado `BodyAdmitted` | tipo | admitido | cobrança depois da leitura; causa reescrita | mutante que cobra depois de ler | B3 | a closure é da S1-C | LIVE |
| B-TRN-04..05 | framing exato; mesmos bytes | CONTRACT L591 / S1-B | transporte | leitura exata + bytes imutáveis | tipo | objeto normal | truncado, byte extra, sem LF, gotejamento; autenticar A e entregar B | mutante off-by-one; mutante verify-then-reread | B3 | — | LIVE |
| B-AUTH-01..03 | preimage canônica == OID | CONTRACT L507, L586–594 / S1-B | objeto | hashlib sobre os bytes entregues | tipo selado | **P2b: paridade 26/26** | objeto corrompido, replace, pack forjado | **P2b: 6 mutantes de preimage RED** | P2b; B3 | SHA-1: colisão não afirmada | MIXED |
| B-RES-01..02 | deadlines de objeto e unidade | CONTRACT L582 / S1-B (valores #320) | unidade | relógio monotônico | loop `select` | dentro do prazo | trava, gotejamento lento, SIGSTOP | mutante com timeout por leitura | **P2: TF5-D**; B3 | valores da #320 | LIVE |
| B-RES-03 | `RLIMIT_AS` antes do exec | CONTRACT L149, L803 / S1-B | Git | `/proc/<pid>/limits` na 1ª ação | `preexec` = `setrlimit` | limite presente | `prlimit` depois do exec | **P2: ablação → unlimited** | P2 PASS; B2 | agregado não afirmado | LIVE |
| B-RES-04..05 | loop único; stderr sem conteúdo | adjudicação / S1-B | unidade | observação sem conteúdo | loop | stderr drenado | flood de stderr; vazamento de conteúdo | mutante sem drenagem; mutante que publica stderr | B3 | — | MIXED |
| B-RES-06 | R4-3 Policy B | D-B-R43 / S1-B (#320) | unidade | causa observada | checagem proporcional pré-spawn | — | causa inferida | mutante que reporta a causa errada | B3 | mecanismo C fora | MIXED |
| B-LIF-01 | dono antes do filho | CONTRACT L843 / S1-B | unidade | atribuição do kernel | reader + `finally` | teardown limpo | TF5-B (sem handle) | **P2: ablação `handle_only` → sobrevivente** | P2 PASS; B2 | — | LIVE |
| B-LIF-02 | subreaper verificado antes do spawn | CONTRACT L838 / S1-B | unidade | `PR_GET_CHILD_SUBREAPER` | `prctl` | neto reapeado | TF5-F (setsid) | **P2: ablação `no_subreaper` → sobrevivente** | P2 PASS; B2 | — | LIVE |
| B-LIF-03 | identidade via pidfd, nunca PID nu | plano S1 / S1-B | unidade | pidfd + prova de filiação | teardown | — | TF5-G (reuso real) | **P2: ablação `bare_pid` → mata processo não relacionado** | P2 PASS; B2 | exige pidfd no domínio | LIVE |
| B-LIF-04..06 | teardown em toda saída; zero sobreviventes; setsid no domínio | CONTRACT L838–846 / S1-B | unidade | varredura por ppid + ECHILD | `finally` | limpo | TF5-A, TF5-C, TF5-D, TF5-E, TF5-F | ablação `handle_only` em TF5-C e TF5-F | P2 PASS; B2 | estado D declarado | LIVE |
| B-LIF-07 | fds lineares (lei #354) | #354 / S1-B | reader | dono pré-existente, `close_once` | latch padrão S1-A | census de fds limpo | reuso numérico, re-close | mutantes do padrão S1-A (M-L3) | B1, B2, B3 | pipes internos do `Popen` declarados | MIXED |
| B-OUT-01..03 | type-state; reason codes fechados; precedência sem perda; sem ativar S_G | CONTRACT §7.1 / S1-B | saída | tipos selados | tabela | — | sucesso com sobrevivente; substituição de causa | mutantes de precedência | B1, B2, B3 | — | MIXED |
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
| Lifecycle | TF5-A (exceção no setup do filho), TF5-B (filho existe e o construtor falha), TF5-C (`BaseException` pós-spawn), TF5-D (travado antes do protocolo), TF5-E (falha pré-exec), TF5-F (setsid), TF5-G (reuso real de PID), falha de close, estado D, erro primário com sobrevivente | **TF5-A..J: P2 PASS em A e B**; estado D: `gate_unavailable` |

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
- mutantes de precedência (B-OUT).

**O harness sai ≠ 0 em falha** (exercitado: execuções 1 e 2 do P2 falharam e foram diagnosticadas; ver `experiments/README.md`).

## 20. Process state machine

```text
UNIT_INIT
 → PRECONDITIONS  (dedicado; subreaper verificado; NNP verificado; procfs admitido; expectativa comparada;
                   snapshot admitido e fchdir; census tipado de fds)       ── falha → REFUSED (sem filho)
 → SPAWNING       (Popen; preexec = setrlimit)                             ── exceção → TEARDOWN
 → CHILD_OWNED    (pidfd; o dono já existia: reader + finally + atribuição) ── BaseException → TEARDOWN
 → PROTOCOL       (request → header → admissão → body → hash)×N            ── recusa, deadline ou exceção → TEARDOWN
 → TEARDOWN       (varredura ppid → pidfd → prova de filiação → SIGKILL → reap; até vazio ou deadline)
 → COMPLETED | REFUSED(reason, primary) | UNIT_TEARDOWN_INCOMPLETE(primary preservada)
```

Toda aresta de saída depois de `SPAWNING` passa por `TEARDOWN`.

## 21. FD ownership table

| Descriptor | Criado por | Dono | Liberação | Herdado pelo Git |
|---|---|---|---|---|
| duplicado do snapshot | API atômica da S1-A (B-HO-02) | `ReaderSnapshotInputV2` (dono linear pré-existente) | `close_once` + latch no fim da unidade | não (CLOEXEC; o cwd vem do `fchdir`) |
| fd de procfs | reader | contexto do reader | fim da verificação | não |
| pipes do protocolo | reader | unidade | após o teardown | só a outra ponta como 0/1/2 |
| pidfd do filho e dos candidatos | teardown | teardown | `close` após o reap | não |
| `/dev/null` somente leitura (se usado) | reader | unidade | fim da unidade | como 0, se for o papel |

A lei da #354 vale para todo fd novo: o novo dono é adquirido antes de o anterior poder falhar ao liberar; close no máximo uma vez; falha registrada no latch, nunca re-close.

## 22. Process ownership table

| Processo | Criado por | Dono | Identidade | Fim |
|---|---|---|---|---|
| reader dedicado | #350 (U3) | #350 | — | fora do escopo da S1-B |
| filho Git | reader (`Popen`) | reader (atribuição do kernel), desde antes de existir | pidfd + prova de filiação | teardown (§14) |
| netos, inclusive `setsid` | Git ou helper | reader (subreaper) | pidfd + prova de filiação | teardown |

## 23. Census contract

**Helper dedicado:** `tests/agent_review/_s1b_census_v2.py` (D-B-CENSUS). **O census congelado da S1-A não é modificado.**

**Proibições estáticas nos módulos S1-B:**
- `shell=True`;
- cópia de `os.environ`;
- reabrir o snapshot por path;
- `cwd=<snapshot path>`;
- `os.kill`/`killpg` como identidade de teardown;
- `Popen` fora do site controlado único;
- `Popen.wait`, `poll` e `communicate`;
- CDLL e `prctl` fora da gramática congelada (wrapper único);
- aquisição de descriptor sem dono pré-existente;
- re-close numérico stale;
- `preexec_fn` que faça algo além de `setrlimit`;
- stderr bruto em estado público ou de resultado;
- import de símbolo privado de `bounded_git_v2`.

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
| `safe.directory` | por comando (§10) | onde existe (2.35.2+); abaixo disso o Git não aplica a checagem, então o valor não é necessário |
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
| **B2: contexto do reader + capability closure + privilégio + lifecycle** | procfs, comparação, imutabilidade, census tipado, NNP, subreaper, spawn A, teardown; roda primeiro contra um filho falso | B-RC-02..04, B-IMM-*, B-CAP-*, B-PRV-*, B-LIF-* | `cat-file` | **positivo cross-principal obrigatório**; #363 resolvida |
| **B3: Git contido + transporte + autenticação de objetos** | `cat-file --batch` relativo ao fd, máquina de estados, deadlines, stderr, R4-3, hash | B-EXE-*, B-TRN-*, B-AUTH-*, B-RES-*, B-OUT-* | closure (S1-C) | paridade com o Git real; #363 resolvida |

Cada slice tem **grant próprio**, na ordem B1 → B2 → B3. Este freeze não implementa nenhuma.

## 28. Claim budget

```yaml
may_claim:   # após a qualificação de cada slice, nunca por este freeze
  - genuine_published_snapshot_handoff
  - descriptor_binding_preserved_across_handoff
  - reader_context_observation_against_explicit_expectation
  - reader_non_mutability_mechanism_within_declared_domain
  - inherited_fd_capability_closure_verification
  - exec_privilege_non_escalation_mechanism
  - descriptor_relative_contained_git_execution
  - strict_git_object_transport_framing
  - zero_body_prefetch_before_admission
  - per_object_resource_enforcement_mechanism
  - lifecycle_ownership_from_spawn
  - zero_survivors_or_typed_refusal
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
| D-B-SPAWN-MECHANISM | DEFER_TO_CAUSAL_SPIKE → **P2: A selecionado** | §14 |
| D-B-USERNS | AUTHORIZED_CONTRACTION | `initial_user_namespace_only` (V1); não é lei universal |
| D-B-GIT-FLOOR | ADOPT | lazy fetch estruturalmente irrelevante (P2b); piso por recurso usado (§24) |
| D-B-SAFE-DIR | ADOPT | escopo de comando (`-c`), valor `*` com justificativa (§10); configuração ambiente proibida |
| D-B-R43 | ADOPT | Policy B; `ReportedCauseRequiresObservedTruthMaker`; números da #320 |
| D-B-PRECEDENCE | ADOPT | `unit_teardown_incomplete > reader_descriptor_close_failed > primary_failure`; diagnóstico primário preservado |
| D-B-CENSUS | ADOPT | helper S1-B dedicado; census S1-A intocado |
| D-B-CI | ADOPT | #363 independente; bloqueia só a qualificação pesada de B2 e B3 |

**Nota para a revisão:** D-B-SAFE-DIR adjudicou "escopo mínimo de comando". Este freeze lê "escopo" como **uma invocação** e escolhe o valor `*` pela razão dada em §10. Se o mantenedor quiser um valor de path, isso reintroduz um pathname como entrada de decisão e precisa de adjudicação explícita.

## 30. Stops and final disposition

### 30.1 P2 (spike causal de spawn, experimento descartável)

```yaml
P2_DISPOSITION:
  SPAWN_MECHANISM_SELECTED:
    mechanism: "A — controlled Popen inside a dedicated reader (lifecycle owned by the reader via kernel attribution)"
    evidence: "experiments/p2_spawn_spike_results.json — 44/44 PASS (runs 3, 4 and 5 all 44/44); experiments/p2b_git_facts_results.json — PASS sha1 and sha256"
    alternatives_rejected:
      B_fork_exec: "closes the same obligations with a larger re-implemented child path; retained as fallback (§14)"
      C_minimal_launcher: "no demonstrated need (LargerMechanismRequiresDemonstratedNeed)"
    unavailable_witnesses: [D_state_process (requires privilege), X1_setuid_filecaps (B2), cross_principal_positive (B2)]
    qualification_transfer: none
```

**Matriz P2** (mecanismo × obrigação × positivo, contramodelo e discriminador × resultado; A e B idênticos):

| Obrigação | Positivo | Contramodelo | Discriminador (ablação RED) | A | B |
|---|---|---|---|---|---|
| OwnerEstablishedBeforeChildExists / NoSpawnToUnownedWindow | POS-base | TF5-B | `handle_only` → sobrevivente | PASS | PASS |
| TeardownCoversEveryExit | POS-base | TF5-A, TF5-C, TF5-D, TF5-E | `handle_only` em TF5-C → sobrevivente | PASS | PASS |
| SubreaperEstablishedBeforeSpawn | TF5-F reapeado | TF5-F | `no_subreaper`; `handle_only` → sobrevivente | PASS | PASS |
| PidIdentityNotBarePid | — | TF5-G (reuso **real**) | `bare_pid` → processo não relacionado morto | PASS | PASS |
| DescriptorInheritanceClosed | fds do filho `[0,1,2]` | TF5-H, TF5-I, TF5-J, TF5-J2 | `no_fd_census` (A: reader `O_RDWR`; B+`no_close`: fd herdado) | PASS | PASS |
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
| Contagem de autoridades antes → depois | autoridades semânticas: **1 → 1** (Git; S1-A; #350). **Projeções no repo** da gramática do `cat-file --batch`: 1 → 2 (C3, leniente, + S1-B estrita). Da preimage canônica: 2 → 3 (`trusted_object_authority_v2.py:818`, `strict_json.git_blob_oid`, + S1-B). A C3 não é reutilizável porque é leniente (F4); as duas preimages existentes têm outras entradas (objeto loose comprimido; só blob sha1). **A consolidação numa primitive única fica declarada como follow-up**, fora do escopo da S1-B | busca | ver abaixo | projeções que divergem | CONTRACT | paridade por projeção |

Busca que sustenta as negativas (escopo `app/` e `scripts/` em `ab92e89`):
- nenhum consumidor de `PublishedSnapshotV2` fora de `physical_snapshot_v2.py`;
- parsers de `cat-file --batch` só em `git_commit_subject_v2.py`;
- preimage só em `trusted_object_authority_v2.py:818` e `app/common/strict_json.py:142`;
- nenhum NNP ou subreaper em `app/`; pidfd só no subsistema #201.

**§3 Language / capability**

| Pergunta | Resposta | Truth-maker | Evidência | Contramodelo | Autoridade | Qualificação |
|---|---|---|---|---|---|---|
| Aceitos explicitamente | Linux com pidfd completo; x86_64/aarch64; CPython 3.11; userns inicial; sha1 e sha256 (piso); objetos commit, tree, blob e tag; loose e pack | §4, §24 | P2, P2b | — | D-B-USERNS, D-B-GIT-FLOOR | B2, B3 |
| Não suportados, e onde está escrito | userns não inicial, rootless, kernel sem pidfd, Git abaixo do piso, estado D (declarado), NFS/FUSE (#320): §4, §13, §14 e §24 deste freeze | idem | — | — | adjudicação | — |
| Decisão implícita restante | nenhuma identificada. O valor de `safe.directory` está explicitado (§10) e marcado para a revisão (§29) | — | — | — | — | revisão independente |

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
| o `_posixsubprocess` só faz `dup2` 0–2 → `preexec` → `close_fds` → exec | TCB CPython 3.11; corroborado pelo P2 (TF5-H em A) |
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
  authority_count_before_after: {semantic: "1 → 1", in_repo_projections: {cat_file_batch_grammar: "1 → 2", canonical_preimage: "2 → 3"}}
  copied_or_projected_git_semantics:
    cat_file_batch_transport_grammar: {authority: real_git, qualification: parity_with_real_git}
    canonical_git_object_preimage: {form: "<type> SP <decimal-size> NUL <body>", authority: real_git, qualification: parity_with_real_git}
  own_pack_reader: false
  declared_gaps_for_B2: [waitid_ECHILD_non_child_candidate_witness]
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
disposition: S1B_ARCHITECTURE_FREEZE_DRAFT_READY_FOR_INDEPENDENT_REVIEW
author_self_review_performed: true
independent_review: NOT_PERFORMED        # author_self_review != independent_review
ArchitectureFreezeReady: false
ImplementationGrant: false
next_authorization: "revisão independente/Codex e adjudicação do exact head deste freeze"
```
