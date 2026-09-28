# #301 S1-A — Architecture Freeze v1 (aquisição física, budget físico e publicação do snapshot)

> Registro de planejamento. Não é normativo sobre `agent-review-v2-301-s0/CONTRACT.md`, não altera produção e não autoriza implementação: `ArchitectureFreezeReady != ImplementationGrant`.

## 1. Exact baseline

```yaml
repository: mglpsw/aiops-orchestrator
master: 5a9a8e5559ebc63e80a36df16d220d1c2910b179     # == origin/master em 2026-09-28 (ls-remote)
master_tree: 427534e16ae9c85999a0e51f479967048e03ba70
owner_issue: 301
predecessors:
  C3:   {issue: 304, state: integrated (PR #349)}
  C4_Q: {issue: 333, state: integrated (PR #352)}
  C4_S0: {pr: 355, merge: 5a9a8e5, source_head: 02f2b66, experimental_head: 34fc575, contract: agent-review-v2-301-s0/CONTRACT.md}
plan_inputs: ["#301 5860375191 (S1 plan candidate)", "5861203238 (post-Ready addendum)",
              "5861631976 (resource scope addendum)", "5863968632 (integration checkpoint)"]
subject_drift: none
this_document: planning record; not normative over CONTRACT.md; no production change
implementation_authorized: false
```

## 2. Predecessor S0 contract (o que é herdado, sem reabrir)

- Arquitetura C: `AdmittedStorageCapability → PrivilegeSeparatedProducer → PhysicalSnapshot(staging) → AtomicPublish(committed/<id>) → …` (CONTRACT L73-164).
- Produtor: `stage: physical_only`; proibidos: git, inflate zlib, validação semântica, prova de hash, parse de pack, verify-pack (L83). Copia loose (bruto) e `pack-*.{pack,idx}`; autora `config` (só formato), `HEAD → refs/heads/none`, `refs/heads` e `objects/info` vazios, recibo (L84-86).
- `S1_obligations` (5 itens, L1219-1256) e `S1_A_STORAGE_ACCESS_CONTAINMENT` (L1270-1278, contramodelo `STORAGE_ROOT_PARENT_PROBE_ESCAPE`/4117560411).
- `ByteAdmissionContainment != ProducerOperationContainment` (L226); `301S_RES: NOT_QUALIFIED_IN_S0` (L246-250).
- Evidência 216/216 permanece ligada a `34fc575`; nada aqui a reexecuta ou transfere.

Correção herdada do plano pré-merge (5860375191): "S1-A closes S1_RES_01" **retirado**; "G1C extended with an optional budget" **retirado** (ver §5 e §29 D-G1C).

## 3. Objective

```text
Dado:
  A = AuthorizedGitStorageSetV2 já estabelecida (C2_A)                 — autoridade de leitura
  L = SourceRepositoryLocatorV2 (nome hostil; não é autoridade)         — onde começar dentro de A
  F = DeclaredGitObjectFormatV2 (sha1 | sha256), declarado pelo caller   — nunca lido da origem
  P = PhysicalWorkBudgetV2 explícito
  W = SnapshotPublicationRootV2 construída a partir de um descriptor já aberto — autoridade de escrita
S1-A só lê/percorre objetos alcançados por DESCIDA a partir de descriptors admitidos por A;
só escreve abaixo de W; cobra em P toda operação física do LADO DA ORIGEM antes ou no consumo
(o lado da saída é limitado por files_copied + constante, §10);
copia só material físico de object store (mais um esqueleto fixo autorado pelo produtor);
publica por um único commit point atômico NOREPLACE; e retorna PublicationOutcomeV2 tipado.
Se qualquer obrigação falhar antes do commit point: NotPublishedV2, sem PublishedSnapshotV2.
```

Assinatura conceitual congelada (nomes finais sujeitos à revisão do source; semântica congelada):

```text
publish_physical_snapshot_v2(*, source_authority=A, source_locator=L, object_format=F,
                             physical_budget=P, publication_root=W) -> PublicationOutcomeV2
```

`DeclaredObjectFormat != AuthenticatedObjectFormat`: S1-A usa `F` só para classificar nomes físicos
(38/62 hex, `pack-<40|64>`) e autorar `config`; o cross-check de `F` contra `ExpectedCommitV2` é de S1-C.

Aplicabilidade (precondições externas; S1-A **não** as prova, sua qualificação é condicional a elas):

```text
S1A_Applicable(A,L,F,P,W) :=
    AuthorizedSource(A)                                   # #331/C2_B
  ∧ AuthorizedPublicationRoot(W)                          # U3: quem abriu o fd de W
  ∧ PublicationStorageSeparatedFromSourceStorage(A,W)     # U3; W ⊂ A misturaria output e origem
  ∧ SourceStorageQuiescentDuringAcquisition(A)            # #331/U3 (CONCURRENT_RENAME_OUT_OF_ROOT)
  ∧ PublicationNamespaceWritableOnlyByProducer(W)         # U3
```

## 4. In-scope / out-of-scope

In-scope: resolução física da topologia (repo locator, `.git` dir, `.git` file `gitdir:`, `commondir`, `objects/`, `objects/info/alternates`); contenção de operações na origem; `PhysicalWorkBudgetV2` único; achatamento de alternates; staging/publicação/commit point; estados pós-commit; ownership de descriptors do fluxo; API mínima aditiva em C2_A.

Out-of-scope (cada item tem owner, §22): `S1_CTX_01`, NNP, execução Git contida, subreaper/pidfd, prazos Git, framing de transporte (S1-B); `S1_SEM_01`, parse/admissão C3, walk da closure, autenticação de commit, `root_tree`, contabilidade lógica por ocorrência, memfd/selo S_G (S1-C); `S1_LIFE_01`; composição/harness dois-principals (S1-D); proveniência de `ExpectedCommit` (#319); política de disponibilidade, valores de budget, NFS/FUSE, R4-3 (#320); proveniência de A/C2_B (#331); sites legados de #354; identidade/provisionamento do produtor, launcher, dono de W, GC de staging (U3); S_D, E, G5, CT104, release, deploy.

## 5. Architecture decision

```text
ANTES (G1C / protótipo S0):  locator/pointer → open a partir de "/" ou de "..": contains_fd sobe por ".." → decide
AGORA (S1-A):                dup(root admitido) → nome hostil casado LEXICALMENTE contra nomes de root
                             → descida openat no-follow componente a componente, cobrada → descriptor admitido
```

- S1-A **não** usa: `contains_fd` (M:1032, sobe `..` sem O_NOFOLLOW, M:1076), `_open_dir_by_segments_no_follow_v2` (M:665, abre a partir de `/` e checa depois, M:722/766), `_looks_like_git_objects_directory_fd_v2` (M:1288, abre `..`/`HEAD`, M:1313), `_copy_objects_dir_fd_v2`, `_parse_alternates_v2`, `_resolve_git_directories_fd_v2`, `root_fds` (M:929, expõe FD interno sem lock). M = `app/agent_review/trusted_object_authority_v2.py@5a9a8e5`.
- S1-A **pode reutilizar** (import intra-pacote, sem modificar) os primitivos de passo único `_open_dir_no_follow_v2` (M:375) e `_open_regular_file_no_follow_v2` (M:430: O_NOFOLLOW|O_NONBLOCK + fstat S_ISREG + limpa O_NONBLOCK), traduzindo seus reason codes. **Não há precedente em `app/`** de import desses helpers privados (só `tests/` e `experiments/` da S0 os importam): o import intra-pacote é decisão nova, registrada como `D-PRIVATE-IMPORT` (§29).
- G1C e o comportamento de `AuthorizedGitStorageSetV2` existente: **inalterados**. Nenhum budget hook na G1C (decisão (o) de CONTRACT §12 L1188 fica superada: a sonda limitada vive no módulo novo).
- `PhysicalStorageResolverV2` / `PhysicalWorkBudgetV2` / `SnapshotPublicationRootV2` / `physical_snapshot_v2`: nomes verificados como inexistentes no repo em `5a9a8e5` (grep em app/tests/docs). O nome existente mais próximo, `_ObjectCopyBudgetV2` (M:326), é privado da G1C e de outro domínio — não reutilizado.

## 6. Two capabilities

```yaml
capability_A_source:
  type: AuthorizedGitStorageSetV2        # C2_A, existente
  role: autoriza QUAIS roots físicos S1-A pode ler/percorrer
  law: "StorageCapabilityEnforcement != StorageCapabilityProvenance"
  S1A_may: consumir (dup dos roots sob lock)
  S1A_may_not: afirmar quem teve autoridade para criá-la (#331/C2_B); construir A a partir de repo_root
  known_boundary: "construtor aceita fds arbitrários e root_fds expõe FD interno (M:909, M:929): provenance de A é C2_B/U3"
capability_W_publication:
  type: SnapshotPublicationRootV2        # novo
  factory: from_directory_fd(fd)          # única factory; NÃO existe from_path
  law: "PublicationRootLocator != PublicationRootAuthority"
  construction:
    - dup(fd) (o caller mantém o seu); fstat → S_ISDIR
    - openat(dup, "staging", O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC); idem "committed"   # pré-provisionados por U3; S1-A não os cria
    - identidade de kernel de cada fd: (mount_id, st_dev, st_ino) via statx(fd, "", AT_EMPTY_PATH, STATX_MNT_ID) (D9)
      STATX_MNT_ID ausente em stx_mask → refusal publication_mount_identity_unavailable (fail-closed)
    - staging.mount_id == committed.mount_id senão refusal publication_cross_mount
      # SameStDev != SameRenameDomain: rename(2) dá EXDEV entre mount points distintos mesmo com o mesmo FS (bind mount)
    - (mount_id, dev, ino) de staging != de committed senão refusal publication_namespaces_not_distinct   # P17
    - retém 3 fds + identidade de cada; nenhum locator/path armazenado
  owns: [root_fd, staging_fd, committed_fd]; close() idempotente; context manager
  S1A_may:
    staging_fd: mkdirat/openat/fchmod/fsync/unlinkat de staging/<id> e descendentes criados por S1-A
    committed_fd: SOMENTE alvo de renameat2, fstatat(<id>) em observe_commit e fsync — nenhuma criação/remoção direta em committed/
  S1A_may_not: qualificar quem abriu fd, modo/dono de staging/committed, ¬RunnerCanMutate (U3/S1-D)
  applicability_precondition: PublicationStorageSeparatedFromSourceStorage(A,W)   # §3; owner U3; S1-A não prova por traversal
  non_claims: [GC de staging]
```

## 7. Physical Git Storage Topology model

Obligation domain único: **"onde estão fisicamente os bytes do object store"**. `PhysicalLayoutResolution != GitSemanticInterpretation`.

```text
SourceRepositoryLocatorV2 ──(casamento lexical com root)──▶ repo_dir (AdmittedDirV2)
repo_dir/.git   ── UM open cobrado (primitivo novo _open_any_no_follow_nonblock: O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC) + fstat do MESMO fd:
   S_ISDIR  → esse MESMO fd vira o fd do gitdir (sem segunda observação de ".git")  (§11.1)
   S_ISREG  → o pointer "gitdir:" é lido desse MESMO fd; relativo a repo_dir   (§11.2)
   ENOENT   → gitdir = repo_dir (layout bare), exige objects/ dir      (§11.0)
   outro/ELOOP → refusal special_file / symlink_rejected
gitdir/commondir ── ausente → commondir = gitdir; presente → pointer relativo a gitdir (§11.3)
commondir/objects ── descida no-follow → primary objects store (depth 0)
store/info/alternates ── pointer(s) relativos ao store → stores depth+1 (§10), recursivo
store: enumeração ÚNICA e cobrada dos filhos: pack/, info/, [0-9a-f]{2}/ ; nada acima do store
```

S1-A NÃO lê nem interpreta: `HEAD`, `refs/*`, `packed-refs`, `config` da origem (formato vem do caller), `hooks`, `shallow`, `commit-graph`, `multi-pack-index`, `info/packs`, `*.promisor`, `*.keep`, `*.bitmap`, `*.rev`, `*.mtimes`, `incoming-*`, `tmp_*`. A detecção bare **não** abre `HEAD` (diferente da G1C M:1202): basta `objects/` como diretório.

## 8. Root-outward resolver contract (`_RootOutwardResolverV2`, privado)

```yaml
session_start:
  - tracker criado ANTES de qualquer operação
  - charge descriptor_opens × A.root_count ANTES da chamada (root_count: propriedade imutável, sem expor fd; D1)
  - roots = A.duplicate_authorized_roots()     # D1; linearização única sob o lock
  - root dups pertencem à sessão (um dono), fechados no finally da sessão; nunca emprestados a um AdmittedDirV2
AdmittedDirV2:                       # sem chain de fds (revisão PF4)
  root_index: int
  components: tuple[str, ...]        # componentes lexicais a partir do root, cada um descido por S1-A
  fd: int                            # UM descriptor, dono único = o AdmittedDirV2
  invariant: "fd foi obtido por dup(root_dup) [se components=()], pela descida cobrada root_dup → components, ou (só para gitdir = repo_dir/.git) pelo probe de §7: openat(repo_dir.fd, \".git\", O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC) cobrado em descriptor_opens E path_components, com S_ISDIR verificado no mesmo fd"
admit(root_index, components) -> AdmittedDirV2:   # ponto de abertura de diretório na origem (o 2º e único outro é o probe de .git, §7)
  if components == (): charge descriptor_opens+1; fd = dup(root_dup)                      # dup cobrado
  else: para cada c_i: pre c_i ∉ {"", ".", ".."}, sem "/" nem NUL
                       charge descriptor_opens+1, path_components+1 (ANTES do syscall)
                       next = openat(current, c_i, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC)
        # a 1ª descida parte de root_dup sem dup (openat não consome o fd da sessão)
        # intermediários: successor registrado ANTES de liberar o predecessor (lei #354)
resolve(base: AdmittedDirV2, pointer: ParsedPointerV2) -> AdmittedDirV2:   # D2
  relative (k × "..", then d_1..d_n):
    if k <= len(base.components):
       target = base.components[:len-k] + (d_1..d_n)   # redução LEXICAL; nada é aberto para "subir"
       return admit(base.root_index, target)            # re-descida desde o root dup, tudo cobrado
    else:                            # sobe acima do root de base
       exige locator do root de base; nome absoluto = normalize_lexical(locator + pops + d_i) → caso absolute
       sem locator → refusal pointer_escapes_capability_root (nenhum open)
  absolute (/c_1..c_m, sem "." e sem ".."):
    candidatos = roots j com locator L_j prefixo de componentes; escolhe o MAIS LONGO
    nenhum → refusal pointer_outside_authorized_storage (nenhum open)
    return admit(j, componentes restantes)
forbidden_ops: [open(".."), open a partir de "/", contains_fd, readlink(/proc/self/fd) como autoridade, stat/fstatat/faccessat/readlink por nome,
                DirEntry.is_dir/is_file/is_symlink/stat (fstatat oculto quando d_type=DT_UNKNOWN),
                fd compartilhado entre dois AdmittedDirV2, dup/open não cobrado]
```

Justificativa do `..` legítimo (fecha `STOP_LAYOUT_POINTER_RESOLUTION_NOT_CLOSED`): cada componente de `base.components` foi descido por S1-A com O_NOFOLLOW|O_DIRECTORY — não há symlink que a redução lexical esteja apagando — e a nova posição é **re-descida desde o root dup**, nunca alcançada subindo. `..` nunca é passado ao kernel. O locator de root (`from_roots`) foi capturado absoluto, sem `..`, e aberto componente a componente no-follow (M:955-968), então o prefixo lexical acima do root é só **nome**, nunca aberto: `PointerNamesRoot != PointerResolvesViaNamespace`. Custo: mais syscalls por pointer, todas cobradas; em troca, ownership trivial (um fd, um dono) e nenhuma operação oculta. A re-descida re-resolve nomes: um rebind concorrente (A7) leva a outro diretório **dentro de A** — autoridade preservada, paridade com o Git não garantida sob mutação concorrente (premissa `SourceStorageQuiescentDuringAcquisition`).

`openat2(RESOLVE_BENEATH)`: considerado e não adotado — em caminhos só-descida não acrescenta garantia (só policia `..`), exige syscall nova via ctypes e kernel ≥5.6.

Limitação nomeada `CONCURRENT_RENAME_OUT_OF_ROOT`: um principal que possa renomear diretórios dentro de A pode mover um diretório já descido para fora do root entre dois `openat`. A claim C1 é de **proveniência de operação** (toda abertura é filho de descriptor admitido no instante do lookup), não de contenção de namespace sob mutação concorrente; quiescência de A é premissa de #331/U3.

## 9. Pointer grammar (D3) — subconjunto estrito do Git, fail-closed

```yaml
common:
  file_open: _open_regular_file_no_follow_v2 (O_NOFOLLOW|O_NONBLOCK; S_ISREG) após charge
  size: fstat.st_size cobrado em pointer_bytes e source_bytes ANTES de ler; leitura limitada a st_size+1; bytes lidos != st_size (crescimento OU encolhimento) → refusal source_changed_during_read (mesma regra da G1C M:613); vale para pointers e cópias
  bytes: sem NUL; decodificação utf-8 estrita (surrogateescape NÃO); componentes não vazios; sem "."; sem "/" repetida
  truncation: nunca silenciosa (X2: manifesto >64 KiB truncado no protótipo) → acima do budget = refusal
dotgit_file:        # git read_gitfile: relativo ao diretório que contém .git
  form: 'gitdir: ' <path> ['\n']       # exatamente uma linha; sem '\r'; sem espaços extras
  relative_base: repo_dir
commondir_file:     # git get_common_dir: relativo ao gitdir
  form: <path> ['\n']
  relative_base: gitdir
alternates_file:    # git: relativo ao objects dir; '#' comentário; linha vazia ignorada
  form: linhas separadas por '\n'; cada linha cobrada em pointer_lines ANTES de interpretar
  quoted_line ('"'-prefixed, C-quoting do git): refusal alternate_quoted_path_unsupported
  relative_base: o store dono do arquivo
path_forms:
  relative: ('..' '/')* d_1 ('/' d_i)*     # '..' só como prefixo; nenhum '..' ou '.' após o 1º componente descendente
  absolute: '/' c_1 ('/' c_i)*             # nenhum '.' ou '..'
over_rejection: declarada — o Git aceitaria, S1-A recusa tipado:
  - quoted alternates; '..' interno; '.'; '/' final; '//'; '\r' / '\r\n'; whitespace extra; bytes não-UTF-8
  - alternate cujo alvo não existe (Git avisa e pula; S1-A: refusal alternate_target_missing)
  - aninhamento de alternates além do limite do Git (constante copiada: 5; Git checa `depth > 5` ao ler um arquivo de alternates,
    com depth 0 = alternates do store primário; erro "nesting too deep" e ignora os mais profundos;
    S1-A conta hops a partir do primário (primário = 0) e recusa o arquivo de alternates lido em hop > 5;
    paridade exata na fronteira (hop 5/6) é caso obrigatório de PARITY_OBJECT_SET;
    S1-A: refusal alternate_nesting_exceeds_git_limit; nunca publica superconjunto do que o Git veria)
  - budget max_alternate_depth, quando menor que o limite do Git, recusa antes (budget, não paridade)
```

Autoridade semântica do layout é **só** o Git; S1-A não pode invocá-lo (CONTRACT L83). No sentido do preflight §2 ("derivar" = perguntar à autoridade e usar a resposta), nem G1C (`_resolve_git_directories_fd_v2`) nem S1-A derivam: ambos **reimplementam** a regra. S1-A é uma **segunda reimplementação, de um subconjunto estrito**, que não é autoridade e não amplia autoridade:

```text
AcceptedLayoutLanguage(S1A) ⊂ LegalLayoutLanguage(Git)
∀ x ∈ corpus declarado ∩ AcceptedLayoutLanguage(S1A):  S1AResolve(x) == GitResolve(x)   (PARITY_LAYOUT, só em teste)
x ∉ AcceptedLayoutLanguage(S1A)  →  over-rejection tipada e segura (nunca ampliação de autoridade)
```

Se a implementação mostrar que o subconjunto não basta para layouts que U3 precisa suportar: `STOP_S1A_REQUIRES_FULL_GIT_LAYOUT_PARITY` (reabre a arquitetura; S1-A não vira clone do Git).

## 10. Physical budget semantics (D4)

```yaml
PhysicalWorkBudgetV2:          # frozen; TODOS os campos obrigatórios, int > 0; sem defaults; ausência → erro de contrato
  max_descriptor_opens:        # S1_RES_01.descriptor_opens
  max_path_components:
  max_entries_scanned:
  max_pointers_followed:       # S1_RES_01.pointer_count
  max_pointer_bytes:           # S1_RES_01.manifest_bytes
  max_pointer_lines:           # S1_RES_01.manifest_lines
  max_alternate_depth:         # S1_RES_01.alternate_depth
  max_source_bytes:
  max_files_copied:
PhysicalWorkTrackerV2: um por publish_physical_snapshot_v2; criado ANTES do primeiro dup; passado a todo resolver/enumerador/copiador
charge_event: "uma ocorrência → exatamente um evento; um evento pode decrementar vários eixos; nenhum evento é revertido"
rule: "charge before or at causal consumption"; excedeu → refusal physical_budget_exceeded{axis} ANTES do syscall/leitura
```

| Operação | Eixos | Momento |
|---|---|---|
| dup de root no início da sessão (`duplicate_authorized_roots`) | descriptor_opens × `A.root_count` (propriedade imutável, D1) | antes da chamada |
| listagem de diretório (`os.scandir(fd)` faz um `dup` interno do fd) | descriptor_opens | antes do scandir; iterador fechado em finally |
| dup de root quando o alvo resolvido é o próprio root | descriptor_opens | antes do dup |
| descida de componente (locator, pointer, re-descida após `..` lexical, pack/, info/, fanout, objects/) | descriptor_opens, path_components | antes do openat |
| probe de `.git` (§7) | descriptor_opens, path_components | antes do openat |
| open de `commondir`, `alternates`, arquivo de objeto | descriptor_opens | antes do openat |
| entrada listada (inclusive ignorada, duplicada, lixo) | entries_scanned | no yield; classificação SÓ pelo nome (nenhum stat); nome candidato → open no-follow cobrado; ELOOP/ENOTDIR → symlink_rejected/tipo errado |
| bytes de pointer | pointer_bytes, source_bytes | fstat antes do read |
| linha de pointer (inclusive vazia/comentário) | pointer_lines | antes de interpretar |
| pointer seguido (gitdir, commondir, cada linha de alternate) | pointers_followed | antes de resolver |
| alternate descido | alternate_depth (depth+1 ≤ max) | antes de abrir o alvo |
| cópia de arquivo | files_copied (antes do open), source_bytes (fstat antes do read) | — |
| ciclo de alternate (dev,ino já visitado) | pointer já cobrado; nada mais aberto | — |
| ocorrência física duplicada (mesmo nome em outro store) | entries_scanned; não aberta nem copiada | primeiro vence (ordem do Git: primário, depois alternates em ordem) |

Escopo da cobrança (F4): lado da origem. Overhead constante por descriptor (`fstat`, `fcntl F_GETFL/F_SETFL` dentro de `_open_regular_file_no_follow_v2`, `fstat` de detecção de ciclo) é coberto pelo evento `descriptor_opens` que o precede. Lado da saída (mkdirat, openat O_EXCL, write, fchmod, fsync, renameat2, fstatat de observe_commit) e factory de W: não cobrados, limitados por `files_copied` + número constante de diretórios/arquivos do esqueleto.

`OutputDeduplication != InputWorkDeduplication`: dedupe de saída não devolve budget. Loose bomb: bytes = tamanho comprimido; nunca inflado. Escrita de saída ≤ source_bytes + esqueleto constante + recibo (limitado pelos contadores); não é eixo próprio. Granularidade declarada: `getdents` pode ler um buffer do kernel à frente do yield — resíduo constante, não proporcional ao input.

`PhysicalSnapshotBudget != ClosureResourceAccounting != GeneralAvailabilityPolicy`: nada de nós, ocorrência lógica, path bytes da closure, prazos, envelope ou memória agregada (S1-B/S1-C/#320).

## 11. Layout pointers (casos)

- **11.0 bare**: `.git` ausente (ENOENT no open no-follow) → repo_dir é o gitdir; exige `objects/` diretório.
- **11.1 `.git` diretório**: o fd do probe de §7 (um open cobrado) é o fd do gitdir; nenhuma segunda abertura de `.git`.
- **11.2 `.git` arquivo**: grammar §9; `resolve(repo_dir, ptr)`; hostile input sem autoridade pelo conteúdo; mesmo budget.
- **11.3 `commondir`**: grammar §9; `resolve(gitdir, ptr)`; o caso legítimo `../..` (worktree `…/.git/worktrees/<n>` → `…/.git`) é redução lexical de `gitdir.components` seguida de re-descida cobrada desde o root; `worktree.useRelativePaths` (`gitdir: ../main/.git/worktrees/wt`) idem.

## 11b. Store enumeration e cópia

Enumeração única por store (sem sonda separada): listagem cobrada em descriptor_opens (dup interno do scandir); cada filho cobrado em entries_scanned e classificado **só pelo nome**; `pack/`, `info/`, `[0-9a-f]{2}` são descidos; store sem nenhum deles → refusal `not_an_object_store` (vale para depth 0 e alternates). Nada acima do store é aberto. Loose: `[0-9a-f]{2}/[0-9a-f]{38|62}` conforme `object_format` do caller; pack: pares completos `pack-[0-9a-f]{40|64}.{pack,idx}` do mesmo store; demais nomes ignorados e cobrados. Symlink em nome que parece objeto → refusal `symlink_rejected` (não skip silencioso). `info/alternates` lido só como pointer; nunca publicado.

## 12. Publication protocol e commit point (D6, D7)

```text
PRE-COMMIT (qualquer falha → cleanup fd-relativo de staging/<id>: fchmod 0700 top-down nos diretórios já finalizados,
            depois unlinkat bottom-up → NotPublishedV2; falha no cleanup → staging_residue=true)

 1 id = secrets.token_hex(16)  (injetável em teste)
 2 mkdirat(staging_fd, id, 0700) exclusivo (EEXIST → refusal snapshot_id_collision); openat no-follow → stage_fd
 3 mkdirat relativo: objects/, objects/pack/, objects/info/, refs/, refs/heads/, fanouts sob demanda (0700)
 4 cada arquivo: openat(dir_fd, name, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600)
     → write loop exato (loop até esgotar; n==0 → refusal short_write_no_progress; EINTR re-tenta)
     → hash-on-write do MESMO buffer (snapshot_fileset_digest) → fchmod 0444 → fsync(file) → close (uma vez)
   (fchmod ANTES do fsync: o modo é metadata do inode e só fica durável se sincronizado depois)
 5 esqueleto autorado (D-SKELETON): config (repositoryformatversion = 0 para sha1, = 1 com extensions.objectformat = sha256
   para sha256, como s0_snapshot_c.py:258-259; bare = true — além de "object format only" de CONTRACT L85),
   HEAD "ref: refs/heads/none\n", recibo por último (§13); mesmo protocolo do passo 4
 5b a sessão de origem (root dups, AdmittedDirV2, iteradores) é fechada por completo, com erros de close processados
    (falha → NotPublishedV2) — nenhuma falha de close da origem pode ocorrer depois do commit point (F29)
 6 diretórios bottom-up, EXCETO a raiz do snapshot: fchmod 0555 → fsync(dir_fd)
 7 fsync(stage_fd)                                   (raiz ainda 0700)
COMMIT POINT
 8 renameat2(staging_fd, id, committed_fd, id, RENAME_NOREPLACE)     # syscall direto (D7)
   retorno 0            → POST-COMMIT
   qualquer retorno ≠ 0 → NÃO se infere nada do errno sozinho; OBSERVAÇÃO fd-relativa (§13 observe_commit):
                          errno só qualifica o reason_code (EEXIST collision; EXDEV cross_mount;
                          EINVAL/ENOSYS noreplace_unsupported; outro rename_failed)
POST-COMMIT (qualquer falha → UnconfirmedPublicationV2; nunca NotPublishedV2)
 9 fchmod(stage_fd, 0555) → fsync(stage_fd)         # stage_fd agora É committed/<id> (mesmo inode; sem reabrir por nome)
10 fsync(committed_fd) → fsync(staging_fd)
11 → CompletePublicationV2
```

Por que a raiz só vira 0555 depois do commit point: no Linux, mover um **diretório** para outro pai exige permissão de escrita no próprio diretório (atualiza `..`; `vfs_rename`: `is_dir && new_dir != old_dir → inode_permission(source, MAY_WRITE)`). O produtor S0 rodava como root e mascarava isso; um produtor não-root com a raiz 0555 recebe EACCES. Consequência: o nome aparece em `committed/` no rename (visibilidade atômica), e a leitura pelo runner só é possível após o passo 9 (raiz 0700 até lá); S1-B/C só recebem `CompletePublicationV2.snapshot` (por tipo).

D7 mecanismo: syscall `renameat2(2)` com `RENAME_NOREPLACE` (=1) invocada **diretamente** via `ctypes` `syscall(2)` com tabela explícita de números por arquitetura (x86_64: 316; aarch64: 276), sem depender do wrapper glibc `renameat2()` (glibc ≥2.28), para que a versão da glibc não vire premissa oculta; arquitetura fora da tabela → fail-closed `publication_atomic_noreplace_unsupported`. Precedente de ctypes/libc no repo: `trusted_check_namespace_kernel_v2.py:104`. Python 3.12 não expõe renameat2. O mesmo mecanismo vale para `statx` (x86_64: 332; aarch64: 291) usado em D9.

Domínio de publicação suportado (declarado, não inferido): Linux ≥5.8 (`STATX_MNT_ID`; `renameat2` ≥3.15), x86_64/aarch64, **filesystem local** que implementa `RENAME_NOREPLACE` (ext4, xfs, btrfs, tmpfs), staging e committed no **mesmo mount**. Filesystems de rede (NFS etc.) estão fora do domínio: lá uma operação pode ter efeito mesmo quando o cliente recebe erro (retransmissão após crash do servidor), e isso já é absorvido pela regra "erro ≠ não publicado" (§13), mas a qualificação não os cobre. Proibido: `if not exists: rename()` e fallback para `rename`/`os.replace`.

Crash (SIGKILL) antes do passo 8: lixo em `staging/`, nunca snapshot em `committed/`; GC do staging é U3 (CONTRACT L1189 (p)). Durabilidade a queda de energia depende do FS honrar fsync: premissa de implantação, não claim.

## 13. Type-state de publicação e PublishedSnapshotV2 (D6, D8)

Regra estrutural (revisão PF1): `PublishedSnapshotV2 ⇔ PUBLISHED_SYNC_COMPLETE`. A capability qualificada **só existe** no ramo completo; os demais ramos carregam um *residual* de outro tipo, que S1-B/S1-C não aceitam por tipo. `IntrinsicValidity != QualifiedCapability`: nenhum consumidor precisa "lembrar" de checar um enum.

```yaml
PublicationOutcomeV2:                      # união fechada; cada variante é um tipo distinto
  CompletePublicationV2:                   # PUBLISHED_SYNC_COMPLETE
    snapshot: PublishedSnapshotV2
  UnconfirmedPublicationV2:                # PUBLISHED_SYNC_UNCONFIRMED: nome em committed/ é nosso; sync/finalize não confirmado
    residual: CommittedSnapshotResidualV2  # fd próprio (para quarentena/diagnóstico), receipt, reason_code; NÃO é PublishedSnapshotV2
  IndeterminatePublicationV2:              # PUBLICATION_STATE_INDETERMINATE: observação impossível ou inconsistente
    residual: PublicationResidualV2        # snapshot_id, reason_code, o que foi/não foi observado; sem fd de snapshot
  NotPublishedV2:                          # NOT_PUBLISHED
    reason_code: str
    exceeded_axis: str | None
    staging_residue: bool                  # cleanup incompleto é reportado, não escondido
observe_commit:                            # usada após QUALQUER retorno ≠ 0 do renameat2 e em interrupção na janela do commit
  ours: fstat(stage_fd) → (dev, ino)
  in_committed: fstatat(committed_fd, id, AT_SYMLINK_NOFOLLOW) == ours
  in_staging:   fstatat(staging_fd,   id, AT_SYMLINK_NOFOLLOW) == ours
  classify:
    in_committed ∧ ¬in_staging: UnconfirmedPublicationV2    # o rename ocorreu apesar do erro reportado
    in_staging ∧ ¬in_committed: NotPublishedV2              # única via para NOT_PUBLISHED após a tentativa de commit
    outro (ambos, nenhum, erro de fstatat): IndeterminatePublicationV2
transitions:
  pre_commit_failure (passos 1-7): NotPublishedV2 (sem tentativa de rename; cleanup fd-relativo)
  renameat2 == 0: POST-COMMIT
  renameat2 != 0: observe_commit
  post_commit_failure (passos 9-10): UnconfirmedPublicationV2
  interrupted_at_commit_point: observe_commit             # BaseException entre retorno do syscall e registro do estado
  base_exception_policy: "Exception → variante tipada; BaseException não-Exception após a tentativa de commit é re-lançada com a variante observada anexada; a POSSE do fd do snapshot/residual é transferida à variante anexada (não é fechado pelo publicador), para que nenhuma variante carregue número de fd já liberado (DescriptorNumber != DescriptorIdentityAfterClose; L9); a variante tem close() idempotente e __del__ que fecha o fd se ainda possuído (mesmo padrão de AuthorizedGitStorageSetV2.__del__, M:1117); se o handler descartar a exceção, o fd é liberado pelo finalizador — o momento da liberação não é garantido (limitação declarada)"
PublishedSnapshotV2:                       # só construível dentro de CompletePublicationV2 (construtor privado com sentinel, padrão TrustedObjectAuthorityV2)
  committed_dir_fd: owned                  # o stage_fd transferido; close() idempotente; context manager
  binding:                                 # em memória; observação, não prova de identidade
    snapshot_id, mount_id, st_dev, st_ino (do fd), st_uid, st_gid (dono observado),
    committed_parent_identity (mount_id, dev, ino)
  receipt: PublishedSnapshotReceiptV2
PublishedSnapshotReceiptV2:                # também gravado no snapshot (agentreview-physical-snapshot-receipt.json)
  schema_version: ar301-s1a.physical-snapshot-receipt.v1
  snapshot_id, declared_object_format      # F declarado; não autenticado (S1-C)
  physical_work: consumo por eixo (os 9) + alternate_sources + max_alternate_depth_seen
  copied_totals: {files_copied, copied_bytes}   # o que entrou no snapshot (≠ trabalho cobrado; D-RECEIPT)
  source_root_identities: [(st_dev, st_ino)]    # rastreabilidade (S0: storage_capability_binding); sem paths
  snapshot_fileset_digest: sha256 de serialização canônica ordenada de (kind, relpath, final_mode, size, sha256)   # NÃO é o container_digest de S_G (CONTRACT L476)
                    de todo arquivo/diretório do snapshot EXCETO o próprio recibo; calculado dos buffers escritos
excluded: [commit, root_tree, refs, source HEAD, source config, remote URL, hooks, promisor, alternate pointer text, locators/paths]
receipt_truth: "o recibo descreve o estado final PRETENDIDO (inclui raiz 0555); só é afirmado verdadeiro em CompletePublicationV2; num residual Unconfirmed pode divergir do disco"
law: "PublishedSnapshotReceipt != ObjectAuthenticityProof"
not_claimed:
  - "quarentena/remoção de committed/<id> residual após Unconfirmed/Indeterminate (owner U3)"
  - "coerência temporal da origem: bytes de arquivos/leituras distintos não são provados coexistentes num único instante (S1-C autentica o que foi copiado)"
  - "F correto para a origem (DeclaredObjectFormat != AuthenticatedObjectFormat)"
```

## 14. Claim Ledger

Campos comuns: `exact_subject: planning@5a9a8e5; qualification@<HEAD exato da futura PR>`; `applicability_domain: domínio de publicação de §12 (Linux ≥5.8, x86_64/aarch64, FS local com RENAME_NOREPLACE, mesmo mount), CPython 3.11 (CI, evidência qualificante) e 3.12 (local, não qualificante), dentro de S1A_Applicable (§3)`; `producer_path: publish_physical_snapshot_v2`; `consumer_path: nenhum hoje (futuro S1-B/S1-C)`.

```yaml
S1A_C1_SOURCE_CONFINEMENT:
  proposition: toda operação na origem é dup de root de A ou openat de componente único (≠ "..") relativo a descriptor obtido por descida cobrada desde um root dup de A; nenhum lookup por nome acima de um root
  authority_or_basis: A (C2_A enforcement); CONTRACT L1270-1278
  mechanism: _RootOutwardResolverV2 (§8); casamento lexical com locators; nenhum open a partir de "/" nem de ".."
  positive_control: repo normal, worktree (gitdir:+commondir ../..), alternate absoluto autorizado, cross-root relativo
  negative_witness: STORAGE_ROOT_PARENT_PROBE_ESCAPE, A2-A11
  anti_vacuity_control: inotify no pai fora de A registra IN_OPEN quando a sonda G1C é reintroduzida (ablation), 0 eventos no S1-A
  limitations: [CONCURRENT_RENAME_OUT_OF_ROOT, "montagens sob root estendem A (U3/#331)",
                "HARDLINKED_INODE_EXTENDS_A: arquivo regular hard-linkado dentro de A alcança um inode também presente fora de A; nlink>1 não pode ser recusado (git clone --local usa hardlinks); requer fs.protected_hardlinks=1 ou política de storage (#331/U3)"]
  discriminator_scope: "inotify observa só opens; lookups sem open (fstatat, faccessat, readlink, openat com ENOENT) são cobertos pelo audit do seam e pelo trace de syscalls (strace -f -e trace=%file) quando disponível; trace ausente → sub-gate gate_unavailable"
  external_owner: "#331 (provenance de A)"
S1A_C2_PUBLICATION_CONFINEMENT:
  proposition: toda mutação é *at relativa a staging_fd/committed_fd ou a descendente criado por S1-A
  mechanism: SnapshotPublicationRootV2.from_directory_fd; identidade (mount_id, dev, ino); staging ≠ committed; nenhuma API recebe path de destino
  positive_control: publicação completa; negative_witness: P12, P15 (sem from_path), P17 (injeção), P19, audit de ops de escrita
  anti_vacuity_control: mutante que escreve via Path → audit acusa dir_fd fora do conjunto
  limitations: dono/modos de W: U3; W∩A = ∅ é precondição de aplicabilidade (§3), não claim
S1A_C3_PHYSICAL_BUDGET:
  proposition: toda operação física do lado da origem gera exatamente um evento de cobrança antes/no consumo, em um único tracker (overhead constante por descriptor coberto pelo evento de open; lado da saída limitado, não cobrado — §10)
  mechanism: §10; tracker único criado antes do primeiro dup
  negative_witness: R1-R14; anti_vacuity_control: mutante "não cobra ignoradas" publica no R1 (falso verde); mutante "re-descida sem cobrança" passa no R14
  limitations: buffer getdents; valores de produção (#320)
S1A_C4_LAYOUT_RESOLUTION:
  proposition: gitdir/commondir/alternates alteram onde bytes são buscados mas nunca criam autoridade
  mechanism: todo pointer passa por resolve() (§8) e grammar (§9); resultado fora de A → refusal sem open
  positive_control: PARITY_LAYOUT (gitdir/commondir resolvidos == `git rev-parse --git-dir/--git-common-dir`, só em teste)
  negative_witness: A3, A4, A10; limitations: over-rejection declarada (§9)
S1A_C5_METADATA_EXCLUSION:
  proposition: nenhum byte de metadata operacional da origem atravessa; o esqueleto é constante autorado
  negative_witness: A13 (hooks, config com remote/promisor, packed-refs, commit-graph, .promisor, .keep, refs)
  anti_vacuity_control: mutante que copia info/ inteiro → teste acusa commit-graph/alternates publicados
S1A_C6_ALTERNATE_FLATTENING:
  proposition: bytes de alternate autorizado entram; o pointer não; objects/info publicado vazio
  positive_control: PARITY_OBJECT_SET (conjunto de objetos da origem via git == do snapshot via git, só em teste)
  negative_witness: A2, R6, R7
S1A_C7_EXACT_WRITE:
  proposition: bytes publicados == bytes admitidos apesar de escritas curtas
  mechanism: write loop; hash-on-write do mesmo buffer
  negative_witness: P1-P3; anti_vacuity_control: mutante "um os.write por bloco" (RC-5 ablation) diverge
S1A_C8_ATOMIC_VISIBILITY:
  proposition: committed/<id> só passa a existir no renameat2; staging nunca é consumível
  negative_witness: P4, P10; limitations: legibilidade pelo runner só após passo 9
S1A_C9_COLLISION_SAFETY:
  proposition: publicação nunca substitui entrada existente em committed/
  mechanism: RENAME_NOREPLACE; sem fallback
  negative_witness: P5, P11; anti_vacuity_control: P6 (rename comum substitui dir vazio pré-existente: ino muda)
S1A_C10_POST_COMMIT_TRUTH:
  proposition: >-
    falha após o commit point, ou erro reportado pelo renameat2, nunca é NotPublishedV2 sem observação
    fd-relativa de que o snapshot continua só em staging; e PublishedSnapshotV2 existe somente em CompletePublicationV2
  mechanism: type-state + observe_commit (§13)
  negative_witness: P8, P9, P14, P18; anti_vacuity_control: mutante que mapeia erro pós-rename (ou errno do rename) em NotPublishedV2;
    mutante que entrega PublishedSnapshotV2 em UnconfirmedPublicationV2
S1A_C12_DURABLE_PUBLICATION_SEQUENCE:
  proposition: a publicação executa exatamente a sequência ordenada de §12 (write → fchmod → fsync por arquivo; diretórios bottom-up fchmod → fsync; fsync da raiz; renameat2; fchmod/fsync pós-commit da raiz; fsync de committed/ e staging/)
  authority_or_basis: S1_PUBLISH_01 (CONTRACT L1251-1256; TF6)
  mechanism: §12
  negative_witness: P7, P16; anti_vacuity_control: mutantes de omissão/reordenação (§17)
  limitations: power-loss real não testável; `PROVED` indisponível; FS honrar fsync é premissa de implantação
S1A_C11_DESCRIPTOR_OWNERSHIP:
  proposition: todo descriptor tem exatamente um dono de cleanup em todo ponto; close ≤1 vez por aquisição
  mechanism: §21; lei #354 aplicada ao código novo
  mechanism_detail: AdmittedDirV2 possui UM fd; root dups pertencem só à sessão; nenhuma chain compartilhada
  negative_witness: L1-L10; limitations: não resolve os sites legados de #354
```

## 15. Obligation Matrix

| Obrigação | S1-A | Claims | Não fecha |
|---|---|---|---|
| S1_A_CORE_PHYSICAL_AUTHORITY | fecha | C1, C4 | — |
| S1_A_STORAGE_ACCESS_CONTAINMENT | fecha | C1 | provenance de A (#331) |
| S1_RES_01.physical_snapshot_subdomain | fecha o **mecanismo** para as 5 dimensões do registro (manifest_bytes, manifest_lines, pointer_count, descriptor_opens, alternate_depth) + entries/bytes/files/components | C3 | remainder de S1_RES_01 = valores adjudicados (CONTRACT L1190 (i)) → #320/U3; coerência admissão ↔ envelope (R4-3) → S1-B + #320; S1_CORE_CLOSURE_RESOURCE_ACCOUNTING → S1-B/S1-C |
| S1_PUBLISH_01.mechanism | fecha (fsync de objetos, config, recibo, diretórios, pais) | C12 (sequência), C7-C10 | power-loss universal |
| S1_CTX_01, S1_SEM_01, S1_LIFE_01 | não toca | — | S1-B/S1-C |

## 16. Countermodel Pack (56)

Authority: A1 `STORAGE_ROOT_PARENT_PROBE_ESCAPE`/4117560411 (alternate que é root de A; `..`/`../HEAD` nunca abertos) · A2 alternate absoluto fora de A · A3 `gitdir:`/`commondir` absoluto fora de A · A4 relativo escapando root (sem locator ou sem root casado) · A5 symlink em qualquer componente/`.git`/objeto/fanout · A6 special file (FIFO em `.git`/`alternates`/objeto: sem bloquear) · A7 rebind de pathname entre descidas · A8 capability fechada antes do dup · A9 capability ausente/tipo errado · A10 pointer não normalizado (`..` interno, `.`, vazio, quoted, NUL, `\r`) · A11 repo locator fora de A (CM-C2-01) · A12 controle positivo cross-root `../..` · A13 injeção de metadata da origem.

Physical resource: R1 3.000 entradas-lixo antes do fanout (K3; fixture assert ordem) · R2 ignoradas não cobradas (witness do mutante) · R3 manifesto > max_pointer_bytes (sem truncamento silencioso, X2) · R4 manifesto com linhas excessivas · R5 pointer com componentes excessivos (TF4) · R6 cadeia profunda de alternates · R7 ciclo de alternates · R8 ocorrência física duplicada · R9 loose bomb (sem inflate) · R10 limite de bytes copiados · R11 arquivo cresce ou encolhe durante leitura · R12 amplificação de opens (X2: 11.903 opens vs 23 cobradas) · R13 limite de arquivos copiados · R14 re-descida após `..` lexical: cada dup/openat cobrado (pointer `../../q` em base profunda consome path_components da re-descida inteira).

Publication: P1 escrita curta forçada (RC-5) · P2 escrita parcial + erro · P3 write retorna 0 · P4 exceção antes do commit (injeção em cada passo 1-7) · P5 colisão em committed/<id> · P6 mutante rename comum · P7 fsync omitido/reordenado (sequência auditada) · P8 fsync falha pós-commit · P9 fchmod da raiz falha pós-commit · P10 SIGKILL antes do commit (lixo de staging) · P11 NOREPLACE não suportado (EINVAL do FS; ENOSYS; arquitetura fora da tabela de syscalls) · P12 staging/committed em devices distintos (`publication_cross_mount`) · P13 colisão de id em staging · P14 interrupção no commit point · P15 ausência estrutural de `from_path` · P16 raiz 0555 antes do rename (EACCES não-root) · P17 `PUBLICATION_NAMESPACE_ALIAS` (staging e committed = mesmo objeto de kernel → `publication_namespaces_not_distinct`; inalcançável no domínio declarado — diretórios não têm hardlink e bind mount tem mount_id próprio, caindo em P19 — logo é witness **por injeção de statx**; a checagem fica como guarda de invariante barata) · P18 renameat2 reporta erro mas o rename ocorreu (injeção: executa o rename real e devolve erro) → UnconfirmedPublicationV2, nunca NotPublishedV2 · P19 mesmo `st_dev`, mounts distintos (bind mount do mesmo FS) → `publication_cross_mount` antes de qualquer staging.

Lifecycle: L1 successor aberto → close do predecessor levanta · L2 close libera e reporta erro · L3 número de FD reutilizado antes de re-close errôneo (canário permanece aberto) · L4 capability fecha durante a aquisição (após dup: prossegue; linearização no dup) · L5 falha no cleanup do staging (`staging_residue=true`), inclusive após diretórios já finalizados em 0555 (cleanup faz fchmod 0700 antes de unlinkat) · L6 censo de FD (`/proc/self/fd`) igual antes/depois em todo caminho · L7 PublishedSnapshotV2 close idempotente e só construível em CompletePublicationV2 · L8 nenhum fd compartilhado entre AdmittedDirV2 (censo de dono por número de fd em todo o resolver) · L9 BaseException após a tentativa de commit: fd transferido à variante anexada, nunca fechado e deixado nela · L10 iterador de scandir abandonado por exceção é fechado (sem leak do dup interno).

Leis #354 aplicadas, sem declarar #354 resolvida: `DescriptorNumber != DescriptorIdentityAfterClose`; `NewOwnerAcquisition must be linearized before PreviousOwnerRelease can fail`.

## 17. Mutation / anti-vacuity plan

| Claim | Mutante causal | Discriminador pretendido |
|---|---|---|
| C1 | reintroduz sonda G1C (`..`/`../HEAD`) ou resolve alternate via `_open_dir_by_segments_no_follow_v2` | inotify IN_OPEN no pai fora de A (kernel; só opens) + trace de syscalls `%file` (lookups) + audit de ops |
| C1 | sonda só-lookup (`fstatat("..")`, `faccessat`) | trace de syscalls / audit (inotify NÃO a detecta — declarado) |
| C2 | escrita de receipt via `Path.write_text` | audit: dir_fd fora de W-derivados |
| C3 | não cobrar entradas ignoradas | R1: publica em vez de `physical_budget_exceeded{entries_scanned}` |
| C3 | ler alternates antes de cobrar | R3: bytes lidos > budget observado no wrapper de read |
| C4 | pointer absoluto que não casa nenhum root é aberto a partir de "/" | A3: trace/audit registra lookup fora de A; inotify no diretório fora de A |
| PARITY | aceitar `..` interno por normalização lexical | PARITY_LAYOUT em fixture gitdir/commondir com symlink-parent (Git resolve fisicamente): resultado diverge |
| C5 | copiar `info/` inteiro | A13: commit-graph/alternates presentes no snapshot |
| C7 | um `os.write` por bloco | P1: bytes/digest divergem da fonte |
| C12 | pular fsync(stage_fd)/fsync de arquivo/reordenar fchmod↔fsync | P7: sequência de syscalls auditada contra §12 |
| C6 | publicar `objects/info/alternates` em vez de achatar | A2/C6: snapshot contém pointer; objetos do alternate ausentes |
| C9 | `os.rename` no lugar de renameat2 | P6: dir pré-existente substituído (ino muda) |
| C10 | erro pós-rename → NotPublishedV2 | P8/P9: variante errada |
| C10 | errno do renameat2 → NotPublishedV2 sem observe_commit | P18: snapshot visível em committed/ reportado como não publicado |
| C10 | PublishedSnapshotV2 entregue em UnconfirmedPublicationV2 | teste de tipo: construtor com sentinel recusa fora do ramo completo |
| C2 | checar só st_dev (sem mount_id) | P19: falha só no rename (EXDEV) em vez de recusa na construção de W |
| C2 | não checar staging ≠ committed | P17 por injeção de statx (inalcançável sem injeção no domínio declarado) |
| C3/C11 | AdmittedDirV2 com chain de fds dup não cobrados | R14 (cobrança) e L8 (dono único) |
| C11 | close do predecessor antes de registrar successor | L1: successor órfão (censo de FD) |
| C12 | fchmod 0555 da raiz antes do rename | P16: EACCES em execução não-root |

Regra (§"Causal mutation discrimination"): cada kill registra que a falha veio do discriminador pretendido; kill por import/fixture não conta. Testes rodam como não-root; se inotify indisponível → `gate_unavailable`, nunca verde. Durabilidade a queda de energia: sem mutante possível, registrado; `PROVED` indisponível. Paridade (§"Positive and negative corpus"): `PARITY_LAYOUT` e `PARITY_OBJECT_SET` usam git **só no teste** como oráculo.

## 18. Derivation Graph

```text
AuthorizedGitStorageSetV2 ──duplicate_authorized_roots()──▶ root dups (tracker criado antes)
  ──▶ _RootOutwardResolverV2: locator → repo_dir → gitdir → commondir → objects (+alternates, recursivo)
  ──▶ stores admitidos (AdmittedDirV2) ──enumeração única cobrada──▶ loose/pack admitidos
  ──▶ cópia exata para staging/<id> (sob SnapshotPublicationRootV2) + esqueleto autorado + recibo
  ──▶ finalize bottom-up + fsync ──renameat2 NOREPLACE──▶ committed/<id> ──pós-commit──▶ PublicationOutcomeV2{PublishedSnapshotV2}
```

## 19. Consumption Graph (futuro; nenhum consumidor hoje)

```text
CompletePublicationV2.snapshot (único tipo aceito) ──▶ S1-B: fchdir(committed_dir_fd) + GIT_DIR=. ; contexto do leitor (U3)
                                                                     ──▶ S1-C: closure autenticada de C; S_G selado
```
Busca em `5a9a8e5`: nenhum símbolo de S1-A existe em app/tests; nada consome hoje.

## 20. Justification Graph

`claim → mechanism → countermodel → discriminator → limitation` = linhas de §14 + §17 (não usar Derivation/Consumption como evidência).

## 21. Descriptor ownership model (D5)

| Descriptor | Criado por | Dono | Liberação |
|---|---|---|---|
| dups de root | `duplicate_authorized_roots` | resolver session (nunca emprestado) | finally da sessão |
| fd de AdmittedDirV2 | `admit` (dup cobrado do root ou última descida) | o AdmittedDirV2 (único) | ao fim do uso do store/pointer |
| intermediários da descida | `admit` | `admit` (transferência linear) | ao registrar o sucessor |
| `.git` probe fd | `_open_any_no_follow_nonblock` | vira fd do gitdir (S_ISDIR) ou é lido e fechado (S_ISREG) | conforme o ramo |
| iterador de `os.scandir(fd)` (dup interno) | enumerador | enumerador (context manager) | finally, inclusive em exceção (L10) |
| fd de pointer / objeto (origem) | open cobrado | função de leitura/cópia | após leitura |
| fd de arquivo destino | openat O_EXCL | passo 4 | após fsync |
| fds de diretórios de staging | mkdirat+openat | publicador | após fsync bottom-up |
| stage_fd | passo 2 | publicador → PublishedSnapshotV2 (Complete) ou CommittedSnapshotResidualV2 (Unconfirmed); fechado em Indeterminate/NotPublished | `close()` do dono |
| root/staging/committed de W | from_directory_fd | SnapshotPublicationRootV2 | `close()` do W (publish nunca fecha) |

Regras: sucessor registrado antes de qualquer release; registro do predecessor limpo **antes** de `close()`; `close()` no máximo uma vez; `OSError` de close = fd liberado + falha (pré-commit → NotPublishedV2 `descriptor_close_failed`), nunca re-close.

## 22. External owners

```yaml
"319": proveniência do commit (ExpectedCommit); S1-A não lê nem aceita commit
"320": política geral de disponibilidade, valores de budget (remainder de S1_RES_01 junto com U3), leituras bloqueantes (P1b NFS/FUSE), memória agregada, R4-3
"331": C2_B — quem produz A; quiescência de A; montagens sob roots
"354": resíduo de transferência de descritor nos sites legados; S1-A só aplica a lei ao código novo
U3: identidade/processo do produtor, launcher, quem abre o fd de W, dono/modos de staging/committed, GC do staging, quarentena/remoção de committed/<id> residual (Unconfirmed/Indeterminate), fs.protected_hardlinks ou política equivalente, prova das precondições PublicationStorageSeparatedFromSourceStorage e PublicationNamespaceWritableOnlyByProducer (§3)
S1-B: contexto do leitor, NNP, Git contido, framing, prazos, envelope, teardown, admissão do estado
S1-C: closure autenticada, contabilidade lógica por ocorrência, path bytes, nós, paridade C3, S_G selado
S1-D: composição e harness de qualificação dois-principals
```

## 23. Claim Budget

```yaml
S1_A_claim_budget:
  may_claim: [S1_A_CORE_PHYSICAL_AUTHORITY, S1_A_STORAGE_ACCESS_CONTAINMENT, S1_RES_01.physical_snapshot_subdomain, S1_PUBLISH_01.mechanism]
  may_not_claim: [S1_RES_01.full, S1_CTX_01, S1_SEM_01, S1_LIFE_01, S1_COMPLETE_AS_COMPONENT, C2_B, C4_COMPLETE, G5,
                  commit/root_tree/closure/S_G authenticated, ExpectedCommit provenance, reader context,
                  immutability to every principal, Git execution contained, universal power-loss durability]
```

## 24. Candidate implementation write-set (NÃO autorizado)

```text
app/agent_review/physical_snapshot_v2.py                 NEW
tests/agent_review/test_physical_snapshot_v2.py          NEW
app/agent_review/trusted_object_authority_v2.py          ADITIVO: root_count, duplicate_authorized_roots(), AuthorizedStorageRootDuplicateV2,
                                                         kw opcional root_locators no __init__, preenchido por from_roots;
                                                         reason code novo STORAGE_CAPABILITY_CLOSED
tests/agent_review/test_trusted_object_authority_v2.py   testes da API nova; suíte C2_A/G1C existente inalterada e verde
```

D1 (API exata):
```python
@dataclass(frozen=True)
class AuthorizedStorageRootDuplicateV2:
    index: int
    fd: int                              # dup (F_DUPFD_CLOEXEC); de propriedade do caller
    dev_ino: tuple[int, int]             # o ligado na construção; fstat(dup) deve coincidir
    locator: PurePosixPath | None        # nome capturado em from_roots; None em from_repository_fd/construtor direto

@property
def root_count(self) -> int:             # imutável desde a construção; não expõe fd; permite cobrar ANTES dos dups

def duplicate_authorized_roots(self) -> tuple[AuthorizedStorageRootDuplicateV2, ...]:
    # sob self._lock: fechada → TrustedObjectAuthorityError(STORAGE_CAPABILITY_CLOSED)
    # dup de todos os roots; fstat(dup) != bound_dev_ino[i] ou falha parcial → fecha os dups, ACQUISITION_FAILED
    # nunca retorna FD interno; não re-resolve pathname; não altera contains_fd/from_roots/close
```
Plural (todos os roots numa aquisição de lock) porque dá um snapshot consistente de A e um ponto único de linearização para L4. Callers verificados: `git_commit_subject_v2.py:67/2065/2235` e `commit_derived_execution_identity_v2.py:297/830/1037` só repassam a instância; nenhum `AuthorizedGitStorageSetV2(...)` direto em app/tests → kw opcional não muda consumers. `from_repository_fd` fica com locator `None` (o `logical_path` é rótulo do caller, não nome capturado).

## 25. Sequence

A0 congelar claims/contramodelos (este doc) → A1 tipos, reason codes, capabilities (incl. API C2_A) → A2 resolver root-outward + grammar → A3 tracker/cobrança → A4 enumeração/cópia/achatamento → A5 staging → A6 commit NOREPLACE → A7 estados/recibo/binding → A8 falhas/lifecycle → A9 mutantes observados RED pelo discriminador → A10 suíte completa + revisão independente no HEAD exato. Witness falhando escrito antes de cada mecanismo.

## 26. Gates

```yaml
exact_subject: required (HEAD da PR; evidência stale se mudar)
source_confinement: {A1_killed_by_inotify: true, lookup_only_probe_killed_by_trace_or_audit: true, audit_no_dotdot: true}
metadata_exclusion: {A13_no_source_metadata_published: true}
parity_oracle: {git_version: recorded_in_evidence, GIT_env_cleared: true, relative_worktree_fixture: "gate_unavailable se git < 2.48"}
interpreter: {qualifying: "CPython 3.11 (CI)"}
physical_budget: {all_scans_charged: true, all_reads_charged: true, ignored_entries_charged: true, one_tracker: true}
publication: {short_write_killed: true, collision_killed: true, pre_commit_never_published: true,
              post_commit_truthful: true, rename_error_observed_not_inferred: true, fsync_sequence_audited: true,
              nonroot_execution: true, same_mount_checked: true, namespaces_distinct_checked: true}
type_state: {PublishedSnapshotV2_only_in_Complete: true}
lifecycle: {owner_linear: true, fd_reuse_countermodel_killed: true, fd_census_clean: true}
parity: {PARITY_LAYOUT: equal, PARITY_OBJECT_SET: equal}
regressions: {C2_A_existing_tests: green, G1C_behavior: unchanged}
independent_review: exact_head
```

## 27. Stop conditions

`STOP_SUBJECT_DRIFT` · `STOP_OWNER_BOUNDARY` · `STOP_LAYOUT_POINTER_RESOLUTION_NOT_CLOSED` · `STOP_PUBLICATION_AUTHORITY_UNMODELED` · `STOP_EXISTING_C2A_SEMANTICS_MUST_CHANGE` · `STOP_S1A_REQUIRES_C2B_PROVENANCE` · `STOP_S1A_REQUIRES_GIT_EXECUTION` · `STOP_S1A_REQUIRES_CLOSURE_SEMANTICS` · `STOP_BUDGET_DOMAIN_EXPANDS_INTO_S1B_OR_S1C` · `STOP_UNRESOLVED_POLICY` · `STOP_S1A_REQUIRES_FULL_GIT_LAYOUT_PARITY` (o subconjunto de §9 não basta para um layout que U3 precisa suportar). Adicional para a implementação: qualquer necessidade de alterar G1C/`contains_fd` ou de invocar git no produtor para de acontecer e reabre este documento. Estado nesta rodada: nenhum disparado.

## 28. STRUCTURAL_CHANGE_PREFLIGHT §§1–7 (títulos do `master`)

Cada item: question / answer / evidence / mechanism / countermodel / remaining_limitation / owner.

1. **Property** — Q: propriedade externa, observação mecânica, disposição conservadora. A: C1-C12 (§14). Evidência: futura suíte no HEAD exato; hoje `DEFINED`. Mecanismo: §§8-13. Contramodelos: §16. Observação: inotify + audit + censo FD + sequência de syscalls + paridade. Conservador: NotPublishedV2 tipado só com observação que o sustenta; erro de rename observado, não inferido; pós-commit nunca rebaixado; capability qualificada só no ramo completo (type-state). Limitação: CONCURRENT_RENAME_OUT_OF_ROOT, power-loss. Owner: S1-A; premissas #331/U3.
2. **Authority** — Q: quem possui a regra; derivar vs reimplementar; nº de autoridades. A: autorização de storage = C2_A (derivada via `duplicate_authorized_roots`, nenhum julgamento próprio de pertinência). Layout: **uma única autoridade semântica, o Git**, antes e depois desta mudança. Git não pode rodar no produtor (CONTRACT L83), então o critério de derivação do preflight (a decisão muda automaticamente quando a autoridade muda) **não** é satisfeito: é **reimplementação**. Contagem: autoridades 1 → 1 (Git); reimplementações não-git 1 (G1C) → 2 (G1C + S1-A). S1-A reimplementa um subconjunto estrito com `AcceptedLayoutLanguage(S1A) ⊂ LegalLayoutLanguage(Git)` e `S1AResolve(x) == GitResolve(x)` no corpus declarado (PARITY_LAYOUT); fora do subconjunto, over-rejection tipada (§9). Uma mudança do Git não atualiza S1-A automaticamente: a paridade em teste é o detector, e ampliar o subconjunto é decisão explícita (`STOP_S1A_REQUIRES_FULL_GIT_LAYOUT_PARITY`). G1C inalterada por decisão (§5); compartilhar parser com a G1C = mudança futura separada. Triggers STOP/REDESIGN do preflight (§"Triggers", L1345-1373), avaliados: (a) recorrência admitida — não (§30); (b) fix falsificando premissa de fix anterior — não estabelecido; (c) input responsável passando a falhar — não se aplica (não há consumidor/input existente); (d) **reproduzir internals de uma autoridade upstream a ponto de virar segunda implementação** — AVALIADO, NÃO ESTABELECIDO: S1-A copia (i) a gramática de um subconjunto estrito dos três formatos de pointer e (ii) uma constante do Git (limite de aninhamento 5); não reproduz a resolução do Git fora do subconjunto (recusa), não reproduz o comportamento de alvo ausente (recusa em vez de pular), e não interpreta config/refs/objetos. O guarda contra deriva para segunda implementação é `STOP_S1A_REQUIRES_FULL_GIT_LAYOUT_PARITY`; a avaliação é confirmada pelo mantenedor em `D-G1C`; (e) teste falhando duas vezes em discriminar — não aplicável antes da implementação. Risco de divergência: só disponibilidade/over-rejection; bytes seguem content-addressed e autenticados em S1-C. Owner: decisão de grant D-G1C (§29).
3. **Language / capability decisions** — aceitos: `.git` dir/arquivo, bare, commondir, alternates absolutos/relativos com `..` prefixado, sha1/sha256 declarados, pares pack/idx, loose. Não suportados (escritos em §9/§11b): quoted alternates, `..` interno, `\r`, whitespace extra, `.rev/.bitmap/.keep/.promisor/.mtimes/commit-graph/midx`, formato lido da origem (F é declarado), FS de rede, staging/committed em mounts distintos, arquiteturas fora de x86_64/aarch64, kernel < 5.8. Decisões antes implícitas agora escritas (review round 2): alvo de alternate ausente → recusa; aninhamento além do limite do Git → recusa; `/` final, `//`, `.`, não-UTF-8 → recusa; classificação de entradas só por nome; versão do git oráculo registrada e `GIT_*` limpo na paridade. Implícito remanescente: nenhum conhecido após três rodadas; próxima revisão deve procurar.
4. **Positive and negative corpus** — rejeitar: §16 A/R/P/L. Passar com **igualdade**: PARITY_LAYOUT (`git rev-parse --git-dir/--git-common-dir` vs resolvido), PARITY_OBJECT_SET (`git cat-file --batch-all-objects --batch-check` origem vs snapshot) para repo normal, bare, worktree absoluto/relativo, alternate absoluto/relativo/cross-root, pool standalone, partial clone. Bound declarado: só os casos exercidos.
5. **Evidence and mutation discrimination** — testes §16; mutantes §17 observados RED pelo discriminador pretendido; predicados admissíveis após implementação: `MECHANICALLY_VERIFIED` (domínio declarado), `MUTATION_DISCRIMINATED` (por claim com mutante), `EMPIRICALLY_SUPPORTED` (corpus); `PROVED` indisponível. Hoje: só `DEFINED`.
6. **Cross-layer assumptions** — "A é íntegra e quiescente" (C2_B/U3, não testável em S1-A: premissa); "staging/committed só graváveis pelo produtor" (U3); "FS honra fsync" (implantação); "kernel exige write em dir movido" (P16 testa); "hard link dentro de A não alcança inode fora de A" NÃO é assumido: HARDLINKED_INODE_EXTENDS_A é limitação nomeada, mitigada por fs.protected_hardlinks=1 ou política de storage (owner #331/U3); "getdents não amplifica além de um buffer" (declarado); "S1-B/C só recebem PublishedSnapshotV2" (garantido por tipo em S1-A, §13); "W ∩ A = ∅" e "A quiescente" (precondições de aplicabilidade §3, owners U3/#331); "mesmo st_dev ⇒ rename possível" é FALSO e não é assumido (mount_id, P19); "erro do rename ⇒ nada aconteceu" é FALSO em FS de rede e não é assumido (observe_commit, P18). Cada "always/never" do diff futuro deve apontar teste ou premissa nomeada.
7. **Snapshot and ownership** — uma decisão por leitura: ponteiros lidos uma vez de fd e interpretados do mesmo buffer; tamanho do fstat e leitura limitada no mesmo fd; digest do mesmo buffer escrito. Segundo consumidor da mesma fonte: G1C lê o mesmo layout com regras próprias (registrado em §2 Authority). Regras copiadas do Git: gramática-subconjunto dos pointers (§9) e a constante de aninhamento de alternates (5, §9); ambas verificadas por paridade em teste, nunca por cópia de expectativa. Ownership de FDs: §21 (um fd por AdmittedDirV2; nenhum fd compartilhado). Coerência temporal entre arquivos da origem: `NOT_CLAIMED` (§13); S1-C autentica o que foi copiado.

## 29. Open decisions — adjudicação

```yaml
D1: {closed: §24, api: "root_count (imutável, sem fd) + duplicate_authorized_roots() -> tuple[AuthorizedStorageRootDuplicateV2]"}
D2: {closed: §8, rule: "'..' é redução lexical dos components (todos descidos no-follow) seguida de re-descida cobrada desde o root dup; acima do root só por nome lexical via locator capturado"}
D3: {closed: §9}
D4: {closed: §10}
D5: {closed: §21}
D6: {closed: §13, form: "type-state: CompletePublicationV2 | UnconfirmedPublicationV2 | IndeterminatePublicationV2 | NotPublishedV2; PublishedSnapshotV2 só no primeiro; erro de rename → observe_commit"}
D7: {closed: §12, mechanism: "syscall renameat2(RENAME_NOREPLACE) direta via ctypes syscall(2), tabela x86_64/aarch64; sem premissa de wrapper glibc; fail-closed fora do domínio"}
D9: {closed: §6, question: "identidade do domínio de rename de W", answer: "(mount_id via statx STATX_MNT_ID, st_dev, st_ino); staging.mount_id == committed.mount_id; staging ≠ committed; sem STATX_MNT_ID → fail-closed"}
D10: {closed: §3, question: "inputs formais", answer: "A, L (SourceRepositoryLocatorV2), F (DeclaredGitObjectFormatV2), P, W"}
D8: {closed: §13}
grant_time_confirmations:        # decisões do mantenedor, não fatos desconhecidos
  D-G1C: "aceitar 2º interpretador de layout (subconjunto estrito, paridade) com G1C inalterada; supera (o) de CONTRACT L1188; aceita a avaliação do trigger (d) do preflight como NÃO ESTABELECIDO (§28.2), incluindo a constante copiada 5"
  D-ROOT-MODE: "raiz do snapshot 0700 até o commit point, 0555 pós-commit (regra de rename de diretório)"
  D-DOMAIN: "domínio de publicação suportado = Linux ≥5.8, x86_64/aarch64, FS local com RENAME_NOREPLACE, staging/committed no mesmo mount"
  D-LOCATOR-FORM: "SourceRepositoryLocatorV2 = caminho absoluto normalizado casado contra locators, ou root(index)"
  D-FD-LOCATOR: "confirmar que PublishedSnapshotV2 como capability de fd (sem locator) REFINA e não reabre S0; CONTRACT L135 canonical_publication_locator: required pertence ao AuthorizedReaderExecutionContext (U3/S1-B), não ao output de S1-A (needs_before_grant de 5860375191)"
  D-RECEIPT: "campos novos de recibo exigem decisão (CONTRACT L1155). Mapa para CONTRACT L99-101: object_format→declared_object_format; physical_bytes→copied_bytes (soma dos tamanhos copiados; NÃO source_bytes, que inclui pointers e leituras não copiadas); physical_entries→files_copied (NÃO entries_scanned, que conta lixo e ignoradas); ambos os contadores de trabalho (source_bytes, entries_scanned) ficam como campos novos; alternate_sources_count→alternate_sources; storage_capability_binding→source_root_identities; published_snapshot_identity→snapshot_id (+ binding em memória: mount_id, dev, ino). Novos: contadores por eixo, snapshot_fileset_digest (nome distinto de container_digest de S_G, CONTRACT L476)"
  D-PRIVATE-IMPORT: "S1-A importa _open_dir_no_follow_v2/_open_regular_file_no_follow_v2 da G1C sem modificá-los (sem precedente em app/); alternativa: reimplementar os dois primitivos no módulo novo"
  D-SKELETON: "config autorado com bare=true e repositoryformatversion 0/1 conforme F; além de 'object format only' (CONTRACT L85)"
  D-PRE-GRANT-DEFERRAL: "CONTRACT L1187 exige (p) e (i) antes do grant de S1; confirmar que o grant de S1-A (só mecanismo, com W de teste e budgets sintéticos) pode ser emitido com (p) e (i) adiados para U3/#320, obrigatórios antes de S1-D/ativação"
contract_L1187_pre_grant_decisions:      # proposta do autor; (p) e (i) dependem de D-PRE-GRANT-DEFERRAL
  "(o)": "superada: nenhuma mudança na G1C (D-G1C)"
  "(p)": "adiada: serviço/identidade do produtor e GC (staging e residuais committed) → U3, exigidos antes de S1-D/ativação; o mecanismo de S1-A é qualificável com W de teste"
  "(i)": "adiada: valores de budget → #320/U3 antes da ativação; a API de S1-A exige valores explícitos e os testes usam valores sintéticos"
  "(ii)": "não se aplica a S1-A (C3 → S1-C)"
  "(iii)": "não se aplica a S1-A (contexto do leitor → U3/S1-B)"
maintainer_adoption:                     # registro de autoridade; NÃO são fatos descobertos pelo agente
  source: "decisão do mantenedor na sessão de planejamento de 2026-09-28, após review_round_4"
  authority_class: maintainer_decision_adopted_by_architecture_freeze
  effect: "as nove confirmações acima passam de pendentes a ADOTADAS para este freeze; não constituem implementation grant"
  adopted:
    D-G1C: {state: ADOPTED, note: "duas reimplementações do layout (G1C existente, inalterada; S1-A subconjunto estrito); Git é a autoridade externa; paridade explícita; preferível a expandir a G1C dentro de S1-A"}
    D-ROOT-MODE: {state: ADOPTED, note: "0700 pré-commit → 0555 pós-commit, consequência do produtor não-root"}
    D-DOMAIN: {state: ADOPTED, note: "domínio inicial estreito e qualificado; nenhuma claim genérica de 'Linux'"}
    D-LOCATOR-FORM: {state: ADOPTED, caveat: "o locator permanece nome e nunca autoridade"}
    D-FD-LOCATOR: {state: ADOPTED, note: "mesma separação identidade/locator"}
    D-RECEIPT: {state: ADOPTED, caveat: "traceability != authenticity permanece invariante do recibo"}
    D-PRIVATE-IMPORT: {state: ADOPTED, caveat: "helpers privados são mecânica compartilhada, não autoridade semântica; a implementação deve revelar cedo se devem virar um helper público mínimo — se sim, é decisão nova, não absorvida silenciosamente"}
    D-SKELETON: {state: ADOPTED, caveat: "constante autorada por S1-A; nunca cópia ou reinterpretação de metadata da origem"}
    D-PRE-GRANT-DEFERRAL: {state: ADOPTED, note: "valores de produção, C2_B/U3, R4-3 e afins ficam fora do primeiro implementation grant"}
planning_gaps: []
```

## 30. Planning disposition

```yaml
review_round_1:
  subject: rascunho inicial deste documento (não commitado; worktree local)
  reviewer: mantenedor (revisão de planejamento fornecida na sessão)
  declared_scope: arquitetura do freeze (capabilities, resolver, budget, publicação, type-state, preflight §§1-7)
  disposition_by_reviewer: S1A_ARCHITECTURE_FREEZE_REVISION_REQUIRED
  findings:
    S1A-PF1_TYPED_POST_COMMIT_CAPABILITY: {class: planning, material: true, disposition: fixed, where: §13 (type-state), §14 C10, §17, §26}
    S1A-PF2_RENAME_MOUNT_AND_FAILURE_SEMANTICS: {class: planning, material: true, disposition: fixed, where: §6 (mount_id, D9), §12 (observe após qualquer erro; domínio local), §13 observe_commit, P18/P19}
    S1A-PF3_UNDECLARED_PHYSICAL_INPUTS: {class: planning, material: true, disposition: fixed, where: §3 (L, F, assinatura; DeclaredObjectFormat != AuthenticatedObjectFormat), D10}
    S1A-PF4_RESOLVER_FD_OWNERSHIP_AND_BUDGET: {class: planning, material: true, disposition: fixed, where: §8 (um fd por AdmittedDirV2; re-descida cobrada), §10, §21, R14, L8}
    S1A-R1_PUBLICATION_NAMESPACE_ALIASING: {disposition: fixed, where: §6, P17}
    S1A-R2_SOURCE_PUBLICATION_SEPARATION_PRECONDITION: {disposition: fixed, where: §3 S1A_Applicable, §6, §22}
    S1A-R3_LAYOUT_INTERPRETER_AUTHORITY_WORDING: {disposition: fixed, where: §9, §28 §2, STOP_S1A_REQUIRES_FULL_GIT_LAYOUT_PARITY}
    minor_source_coherence_not_claimed: {disposition: fixed, where: §13 not_claimed, §28 §7}
    minor_glibc_premise: {disposition: fixed, where: §12 D7 (syscall direta)}
  recurrence_questions: "primeira rodada do loop; nenhum candidato de recorrência pode se formar (não há correção anterior declarada)"
review_round_2:
  subject: texto após correções do round 1 (não commitado)
  reviewer: subagente independente read-only (Claude), 2026-09-28
  declared_scope: "12 itens: autoridade por locator; operação não cobrada/cobrada tarde; 2º interpretador como autoridade; parent probing; budget fragmentado; commit point / NotPublished / type-state; ownership de fd; overclaim; fronteiras S1-B/C/#319/#320/#331/U3; consistência interna; fatos citados (linhas M/CONTRACT, Linux, Git); cobertura preflight §§1-7"
  disposition_by_reviewer: REVISION_REQUIRED (0 BLOCKING, 18 MATERIAL, 11 MINOR)
  validated_by_author: "F13 (CONTRACT L476), F17 (CI python 3.11), F20 (nenhum import em app/) conferidos no source; F2 reproduzido pelo revisor em CPython 3.12.3"
  findings_fixed:
    F1: {what: "dups de root antes da cobrança", fix: "root_count imutável; cobrança antes da chamada", where: §8, §10, §24}
    F2: {what: "os.scandir(fd) faz dup oculto", fix: "listagem cobrada; iterador com dono; L10", where: §10, §11b, §21, §16}
    F3: {what: "DirEntry.is_* faz fstatat oculto", fix: "classificação só por nome; open no-follow cobrado", where: §8 forbidden_ops, §10}
    F4: {what: "C3 mais amplo que o mecanismo", fix: "C3 = lado da origem; overhead constante e lado da saída declarados", where: §3, §10, §14}
    F5: {what: "inotify cego a lookups", fix: "trace %file + audit; escopo do discriminador declarado", where: §14 C1, §17, §26}
    F6: {what: "hardlink estende A", fix: "limitação HARDLINKED_INODE_EXTENDS_A; owner #331/U3", where: §14 C1, §22}
    F7: {what: "§2 chamava reimplementação de derivação", fix: "reimplementação de subconjunto; contagem 1→2", where: §9, §28 §2}
    F8: {what: "decisões de linguagem implícitas", fix: "over_rejection completa; oráculo git fixado", where: §9, §26, §28 §3}
    F9: {what: "mutante C4 não violava C4", fix: "mutante de autoridade A3; `..` interno movido para PARITY", where: §17}
    F10: {what: "S1_PUBLISH_01 sem claim dona", fix: "C12 DURABLE_PUBLICATION_SEQUENCE", where: §14, §15, §17}
    F11: {what: "P17 inalcançável", fix: "witness por injeção de statx, declarado", where: §16, §17}
    F12: {what: "fd fechado deixado em variante anexada", fix: "posse transferida à variante; L9", where: §13, §16}
    F13: {what: "container_digest colidia com S_G", fix: "snapshot_fileset_digest; D-RECEIPT", where: §12, §13, §29}
    F14: {what: "remainder de S1_RES_01 sem dono", fix: "remainder = valores (#320/U3) + R4-3 (S1-B/#320)", where: §15, §22}
    F15: {what: "confirmações pré-grant omitidas", fix: "D-FD-LOCATOR; (o)/(p)/(i)/(ii)/(iii) adjudicadas", where: §29}
    F16: {what: "residual committed sem dono", fix: "owner U3; not_claimed", where: §13, §22}
    F17: {what: "domínio Python errado", fix: "3.11 qualificante (CI), 3.12 local", where: §14, §26}
    F18: {what: "cleanup impossível após 0555", fix: "fchmod 0700 antes de unlinkat; L5", where: §12, §16}
    F19-F29: {what: "minors (contagem, precedente de import, probe .git, wording chain, committed_fd, P11/config, short read, recibo pretendido, tabela de mutantes, nome da obrigação, close da origem após commit)", fix: aplicados}
  recurrence_questions:
    candidate_formed: "sim — F1/F2 (dup não cobrado) versus a correção PF4 do round 1 ('toda operação passa pelo tracker')"
    qualification: OUT_OF_SCOPE
    established: "a proposição corretiva de PF4 era o re-descent do resolver e a eliminação da chain de fds; os witnesses de F1 (dup de root da sessão, desenho D1 do round 0) e F2 (dup interno do scandir, pré-existente ao round 1) estão fora do Δ1 de PF4; nenhuma correção declarada falhou no que declarou"
    other_findings: "nenhum outro candidato de recorrência formado"
review_round_3:
  subject: texto após correções do round 2
  reviewer: mesmo subagente independente read-only (verificação das correções + regressões; mesmo escopo de 12 itens)
  result: "F1-F29: 28 FIXED, F6 PARTIAL; novos: N1, N2 MATERIAL; N3-N8 MINOR; 0 BLOCKING"
  fixes:
    N1: {what: "§28.2 dizia que STOP/REDESIGN é só recorrência", fix: "os 5 triggers avaliados; trigger 'segunda implementação' AVALIADO, NÃO ESTABELECIDO, com guarda e D-G1C; constante 5 registrada como regra copiada em §28.7"}
    N2: {what: "(p)/(i) adiadas por decisão do autor", fix: "D-PRE-GRANT-DEFERRAL como confirmação do mantenedor"}
    N3: {what: "§11.1 e invariante de admit contradiziam o probe", fix: "probe declarado como 2º sítio de abertura, cobrado em opens e components"}
    N4: {fix: "C1-C12; C11 witnesses L1-L10; D1 com root_count"}
    N5: {fix: "mapa D-RECEIPT corrigido; copied_totals no recibo"}
    N6: {fix: "finalizador declarado; momento de liberação como limitação"}
    N7: {fix: "limite 5 e convenção de profundidade; fronteira em PARITY_OBJECT_SET"}
    N8: {fix: "passo renumerado 5b"}
    F6_carry: {fix: "HARDLINKED_INODE_EXTENDS_A em §28.6"}
  recurrence_questions:
    candidate_formed: "sim — N3 (regressão da correção F21) e N1 (efeito da correção F8)"
    qualification: OUT_OF_SCOPE
    established: "N3 é inconsistência textual deixada pela correção F21 (o mecanismo de F21 — um único open — está correto; faltou alinhar §11.1 e o invariante); N1 é classificação incorreta de um trigger, não falha de uma correção declarada; nenhuma correção declarada deixou de fazer o que declarou"
review_round_4:
  subject: texto após correções do round 3
  reviewer: mesmo subagente independente read-only (mesmo escopo de 12 itens)
  result: "N1-N8 e F6-carry FIXED; 0 BLOCKING, 0 MATERIAL; 3 MINOR (RR4-m1 probe sem path_components na tabela; RR4-m2 D-G1C sem a avaliação do trigger (d); RR4-m3 'duas rodadas')"
  disposition_by_reviewer: READY
  minors_fixed_after_review: [RR4-m1 (§10), RR4-m2 (§29 D-G1C), RR4-m3 (§28.3)]    # edições de texto, não revisadas de novo
  epistemic: "NON_REFUTED apenas dentro do escopo declarado de 12 itens; nenhum PROVED"
  recurrence_questions: "nenhum candidato formado"
post_review_edits:                       # ReviewedSubject != ModifiedSuccessorSubject
  - "RR4-m1..RR4-m3 (minors do round 4): §10, §29 D-G1C, §28.3"
  - "§29 maintainer_adoption e este bloco (registro de decisão, sem mudança de claim/contramodelo/mecanismo)"
final_read_back:
  reviewer: subagente independente read-only novo (não participou dos rounds 2-4)
  scope: "RR4-m1..m3, maintainer_adoption, post_review_edits/disposition_conditions; integridade estrutural (30 seções + 11b, C1-C12, 56 contramodelos, §28 §§1-7, IDs referenciados definidos, ausência de palavra de fechamento de issue)"
  first_pass: "FINAL_DOC_NOT_CLEAN: F1 MATERIAL (condição de grant exigia master == 5a9a8e5, insatisfazível após o próprio merge docs-only — classe de autoancoragem de 70ba4e3); F2 MINOR (IDs R1-R3 do round 4 colidiam com contramodelos)"
  fixes: {F1: "condição reescrita como delta 5a9a8e5..master restrito ao merge docs-only", F2: "renomeados para RR4-m1..m3", observation_D_G1C: "nota precisada"}
  confirmation: "FINAL_DOC_CLEAN (mesmo leitor; F1 satisfazível e fail-closed; nenhum R1-R3 residual para os minors; nenhuma outra seção alterada — verificação estrutural e por posição de linha, não diff byte a byte das linhas 1-669, por ausência de cópia anterior)"
disposition: S1A_ARCHITECTURE_FREEZE_READY_FOR_HUMAN_IMPLEMENTATION_GRANT
disposition_conditions:
  - "as confirmações de §29 foram adotadas pelo mantenedor (maintainer_adoption); não substituem o implementation grant"
  - "o implementation grant só nasce contra o freeze versionado em master (sha e blob exatos após merge docs-only e read-back)"
  - "master revalidado no momento do grant; se o delta 5a9a8e5..master contiver algo além do merge docs-only deste freeze (em particular qualquer toque em app/agent_review/trusted_object_authority_v2.py, tests/agent_review/** ou docs/engineering/agent-review-v2-301-s0/CONTRACT.md), STOP_SUBJECT_DRIFT e re-derivação"
law: "ArchitectureFreezeReady != ImplementationGrant"
implementation_started: false
implementation_authorized: false
```
