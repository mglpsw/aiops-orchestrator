# #301 S0 — índice de evidência

Índice das afirmações materiais de [`CONTRACT.md`](CONTRACT.md). **Não é norma**: aponta para o
contrato e para os artefatos; não o repete. Hashes provam integridade dos arquivos, não autoridade
nem suficiência.

## Subject e ambiente da execução registrada (head experimental `34fc575`)

```yaml
engine_subject: {commit: 9abcde6420a59b814b5faaff10ca5904c5d23370, tree: 93143d70ed410771776f5f2cdb48d7f8e3f5ed9b}
runtime: {image: "python:3.11-bookworm@sha256:b99029c95d3d37fb1e4e76d287f7984373dca77c665885986e31b2c95260c13c",
          python: 3.11.16, git: 2.39.5, glibc: 2.36, kernel: 6.18.33.2-microsoft-standard-WSL2,
          work_fs: tmpfs, yama_ptrace_scope: 1, actor_uid: 2000, tcb_owner: root,
          privilege: "container uid 0 = real uid 0 (docker without userns-remap); runner = uid 2000"}
architecture: C_PRIVILEGE_SEPARATED_IMMUTABLE_SNAPSHOT   # producer uid 0 publishes; reader runs as uid 2000
wheels: iguais aos sha256 do lock (environment.json)
command: bash experiments/run_py311.sh <checkout> 9abcde6420a59b814b5faaff10ca5904c5d23370 <results>
results: experiments/results/py311/
scripts: experiments/results/py311/SCRIPTS.sha256   # hashes gravados ANTES da execução registrada e conferidos depois (sha256sum -c)
outcome: 216/216 casos com expectativa passaram (10 scripts; captura pela arquitetura C; exp_arch_c 45/45);
         2 observações sem expectativa em exp_process_channel e 1 em exp_structure
         (non_utf8_name_S_vs_C3); único stderr = aviso esperado do zipfile no fixture de membro
         duplicado (exp_deps.stderr)
reproduction: própria (esta sessão); não é reprodução independente
```

Os valores esperados estão escritos em cada script antes da execução registrada. Execuções de
ensaio em CPython 3.12.3 no host e as execuções anteriores em 3.11 **não** são evidência registrada
do head experimental `34fc575`; as dos heads `3d426e1`, `1e2453e`, `1299b00` e `cf69fbf` (arquitetura A) continuam no histórico git. O Spike B (host 3.12/git 2.43) e o Spike C (comentário 5858608157; container efêmero, PID ns com
`CAP_SYS_ADMIN`) foram evidência de viabilidade e **não** são transferidos: a execução registrada
aqui reexecuta C1–C10 e o novo C11 com o código do head experimental `34fc575`. O registro de `8c4842b` (arquitetura B,
171/171) fica no histórico git; os resultados de `exp_snapshot.json` do head experimental `34fc575` reexecutam o código
de B, que continua **rejeitado**, e só sustentam as linhas que dizem respeito a componentes que C
reutiliza (hash-on-read, prazo por objeto).

**Escopo após a decisão do mantenedor (CONTRACT, topo):** as linhas marcadas **S_G** sustentam a
claim de S0; **E** e **S_D** são evidência de viabilidade/protótipo, não qualificação.

## Afirmações → evidência

| Afirmação (CONTRACT) | Arquivo / casos | Resultado | Origem | Limite |
|---|---|---|---|---|
| `git cat-file` serve bytes que não hasheiam ao oid (rc 0) | `exp_n1_auth.json` `*_HOR_git_itself_serves_swapped_blob_rc0` | sha1 e sha256: sim | reproduzida aqui (spike HOR herdado, 3.12) | objetos loose |
| Adulteração de commit/tree/blob recusada pelo mismatch do objeto certo | `*_blob_swapped_*`, `*_tree_swapped`, `*_commit_swapped` | `object_hash_mismatch/{blob,tree,commit}` | aqui | loose; pack/alternates só por argumento |
| Discriminador é o hash | `*_ABLATION_verify_disabled_*` | mutante aceita e incorpora `EVIL` | aqui | — |
| Hash de uma leitura + cópia de outra reabre a janela | `MUTANT_verify_then_reread_embeds_other_bytes` | bytes diferentes | aqui | — |
| Formato do objeto vem do `C` esperado | `expected_sha256_id_against_sha1_repo`, `sha256_*` | `object_format_mismatch`; sha256 aceito | aqui | git 2.39 |
| N1 contra G1/Q no master integrado | `INHERITED_N1_G1_Q_accepts_EVIL_after_private_copy_swap` | `SUCCESS` para `EVIL` | **reexecutada** (spike PR352#5848970869) | viola a quiescência de Q; testemunha para S |
| Fidelidade A/A2 | `exp_structure.json` `parity_*`, `countermodel_*` | paridade exata com árvore declarada e com C3; 7/7 distinções; mutante com perda colide 5/7 | aqui | fixture sintética |
| Recusas estruturais | `gitlink_refused`, `noncanonical_mode_*`, `dotdot_*`, `duplicate_name_refused` | recusados | aqui | regras espelhadas do C3 (CONTRACT §11.2) |
| Pós-compromisso: 9 operações de escrita negadas | `exp_capture_stability.json` `sealed_attack_battery` | EPERM; `mprotect` EACCES; `MAP_PRIVATE` só COW; `F_GET_SEALS=0xf` | aqui | kernel 6.18 |
| Janela pré-selo; selo estranho; mapeamento retido | `pre_seal_*`, `ABLATION_no_post_seal_rehash_*` | detectado / `seal_failed`; mutante `COMMITTED` | aqui | — |
| M mutado após captura; consumidor não lê M | `M_mutated_*`, `consumer_*` | digest igual; `trusted`; 0 aberturas | aqui | `cwd=M`, `PYTHONPATH=M` oferecidos |
| **SNAP (código de B, rejeitado; reexecutado)** — snapshot sem Git e sem busca (**S_G**) | `exp_snapshot.json` `Q1_snapshot_runs_no_git_and_no_fetch`, `Q2_*`, `Q6_*` | 0 processos Git no snapshot; marcador de `uploadpack` ausente; sem remoto no snapshot; `object_missing` tipado; clone parcial com closure completa admitido; o leitor roda só `rev-parse`/`cat-file` com cwd no snapshot | aqui | autorização do host para as raízes: #331 |
| **SNAP (código de B, rejeitado; reexecutado)** — snapshot adulterado depois de tirado (**S_G**) | `Q3_*` | loose trocado → `object_hash_mismatch`; objeto malformado → `transport_deadline` em 3,0 s; **ablação sem prazo trava** (morto aos 15 s); `.idx` forjado → `object_hash_mismatch`; pack corrompido → `object_truncated` | aqui | sem `verify-pack` no caminho de S_G |
| **SNAP (código de B, rejeitado; reexecutado)** — identidade de aquisição imutável (R4-2) | `Q4_*` | root_tree/commit trocados após a aquisição → `sealed_identity_mismatch`; `Subject` congelado; controle comprometido | aqui | integridade do registro sob a premissa **P** do produtor |
| **SNAP (código de B, rejeitado; reexecutado)** — sha256 pelo snapshot | `SHA256_*` | captura aceita com snapshot `sha256`; adulteração recusada | aqui | — |
| **SNAP (código de B, rejeitado; reexecutado)** — duas classes de orçamento | `Q7_*` | store de 50,3 MB / subject de 1 blob → `snapshot_refused`; subject de 48 MiB → `budget_payload_bytes` com snapshot aceito | aqui | — |
| **SNAP (código de B, rejeitado; reexecutado)** — identidade do snapshot (rastreabilidade) | `snapshot_identity_distinguishes_snapshot_from_S_G` | mesmo store → mesmo recibo; store alterado → recibo diferente, S_G igual | aqui | recibo não é trust root |
| **SNAP (código de B, rejeitado; reexecutado)** — censo de órfãos | `no_orphan_processes_or_listeners_remain` | nenhum processo nem socket em escuta novo | aqui, namespace de PID do container | no host o censo vê outras sessões |
| Corpus real capturado pela arquitetura C | `exp_functional.json` `S_G_digest_equals_architecture_A_record_on_this_corpus` (`capture_architecture: C`) | `95504743…` igual ao registro anterior; paridade funcional de E mantida | aqui; produtor uid 0 (`producer_functional.json`), leitor no processo de EXP-FUNC | controle positivo **neste corpus**, não equivalência universal |
| **B/iv** — kill vs fechar-e-esperar (R4-4) | `exp_resources.json` `ABLATION_close_and_wait_refusal_path_waits_for_transport` | mutante espera 8,0 s; kill < 4 s | aqui | — |
| **A** — filho `git` contido pelo kernel (**S_G**) | `exp_resources.json` `git_child_contained_*` | tree 66,7 MB e commit 64 MiB: sem envelope o filho vai a 68,2/68,0 MiB; com `RLIMIT_AS` 64 MiB fica em 11,0 MiB; controle aceito | aqui; medido **no filho** (processo novo por caso), pai lento de 1 s | envelope de teste 64 MiB; valor de produção a adjudicar |
| **A** — corpus real sob o envelope (**S_G**) | `exp_functional.json` `real_corpus_accepted_with_git_child_inside_envelope` | aceito pela captura de C; `ru_maxrss` dos filhos 16,0 MiB ≤ 128 MiB (limite superior: herda RSS pré-`exec`) | aqui | — |
| **B** — transporte estrito (**S_G**) | `strict_transport_*` (9) | cada cabeçalho hostil recusado antes de cobrar ou usar o corpo (o leitor pode pré-ler até um bloco de 64 KiB do corpo ao procurar o fim do cabeçalho: 4117693716; não prova "zero bytes do corpo antes da admissão"); filho morto e colhido na hora; heap ≈ 0 | aqui; transporte falso | injeção num `git` real reproduzida pela revisão da rodada 3 |
| **C** — o objeto comprometido é o autenticado (**S_G**) | `exp_capture_stability.json` `authenticate_A_substitute_B_*`, `ABLATION_no_post_seal_revalidation_*`, `node_not_in_acquisition_record_refused`, `mutate_unsealed_S_then_seal_refused` | `sealed_binding_mismatch`; ablação compromete `EVIL`; `sealed_record_mismatch`; `sealed_content_mismatch` | aqui | tree oids vinculados pelo registro, não re-hasheados pós-selo |
| **D** — limite de componente explícito (**S_G**) | `exp_structure.json` `component_256_*`, `C3_parity_under_same_limit_300`, `C3_also_refuses_*`, `no_admission_limit_refused` | 300 admite, 255 recusa, C3 igual sob o mesmo limite; ausência recusada | aqui | — |
| **E** — SHA | `exp_n1_auth.json` `sha1_*`, `sha256_*` | positivos e adulterações nos dois formatos | aqui | **nenhum teste de segunda pré-imagem**; sha1dc não alegado (CONTRACT §2) |
| Substituição de binding (**S_G**) | `binding_*` (7) | recusas esperadas, inclusive rótulo de algoritmo incoerente (R2-4); driver não executou | aqui | — |
| Pipe forjável; socketpair não | `exp_process_channel.json` | `FORGED` / `ENXIO` | aqui | — |
| Não-ancestral mesmo UID vs consumidor | `same_uid_non_ancestor_vs_*` | sem attach/`/proc/pid/mem`; lista FDs se dumpable | aqui | Yama=1; produtor/launcher não observados |
| Startup: `pyvenv.cfg`, `LD_PRELOAD`, `-I` vs `-S` | `exp_bootstrap_env.json` | conforme esperado (14 casos) | EXP-F1 **reexecutada** + novos | — |
| Piso root-owned | `floor` (23 caminhos), `floor_all_root_owned_and_not_writable` | `true` | aqui | **só neste container** |
| O probe do piso testa o próprio arquivo (R2-6) | `floor_probe_detects_writable_file_in_readonly_dir` | novo probe `true`; probe da rodada 2 `false` no mesmo arquivo | aqui | — |
| S_D (protótipo, **não qualificado**): 25 recusas e controles | `exp_deps.json` | todas as razões esperadas; controle puro e nativo sem caminho aceitos | aqui | wheels sintéticos; ver R2-1/2/3/7/8 |
| Membro-bomba comum recusado antes de inflar | `compressed_member_refused_before_inflate` | 200 MiB recusado, heap 0,27 MiB | aqui | **não** cobre `METADATA`/`RECORD` (R2-1) |
| S_D: defeito R2-1/R2-8 no protótipo congelado | `experiments/sd_future/repro_r2.result.json` | `METADATA` 200 MiB → 2.282 MiB; `RECORD` → 1.001 MiB + crash; superconjunto de tags aceito | reprodução (host 3.12) sobre `s0_deps.py` inalterado desde `1e2453e` | contramodelo obrigatório da slice S_D |
| Mecanismo proposto para S_D | `experiments/sd_future/spike_bounded_archive.result.json` | todos os contramodelos recusados com heap ≤ 0,27 MiB; controle aceito | spike descartável (host 3.12) | não implementado; não é claim de S0 |
| Engine real só de S_G/S_D com paridade | `exp_functional.json` `positive_E_result_equals_normal_path` | `bundle_sha256` igual; `ready`; 2 chunks | aqui | Router → resposta sintética |
| E: 0 aberturas em checkout/venv, 0 subprocessos, 0 distribuições | `E_no_*`, `E_distribution_metadata_visible` | vazios | aqui | audit do bootstrap: só nível Python; vale sob **P** |
| O auditor detecta leitura relativa do checkout (R2-5) | `auditor_detects_relative_checkout_read` | detectada; caminho cru relativo; resolvido sob o checkout | aqui | `os.open(dir_fd=)` sem `dir_fd` no evento |
| Checkout, `.pyc`, venv, plugin afetam só o caminho normal | `checkout_source_tampered`, `pyc_planted_*`, `venv_installed_*`, `pydantic_plugin_*` | normal executa; E inalterado | aqui | — |
| D vinculado ao lock; sem fallback | `D_not_bound_*`, `D_absent_no_fallback` | recusa; `No module named` | aqui | vínculo de rótulo |
| D não estende pacotes de S/stdlib nem sombreia stdlib | `D_cannot_extend_S_or_stdlib_packages` | `ModuleNotFoundError` ×3, `LookupError`, `json` da stdlib | aqui; **defeito reproduzido pela revisão no head `3d426e1`** | — |
| Produtor importa C3 só da cópia root-owned | `run_py311.sh` (arg `/opt/toolrepo-tcb`), `exp_functional.py` | — | construção | observado por configuração, não por auditoria |
| Censo por origem; `pwd` atribuído | `census` | 35 S, 66 D, 2 nativos; `pwd` via `zoneinfo`→`sysconfig`→`expanduser` | aqui | um percurso |
| Recursos de S/D/E | `resources` | CONTRACT §8 | aqui | este corpus/ambiente |
| Commit/tree/blob/expansão/profundidade limitados antes | `exp_resources.json` | tree 16,7 MB, commit 16 MiB, blob 32 MiB recusados com heap 0,06 MiB; expansão 125 MiB recusada em 17,8 MiB; profundidade 110 | aqui | — |
| Falhas sem S parcial; ownership linear | `write_failure_*`, `seal_failure_*`, `mid_capture_*`, `launcher_pre_spawn_*`, `hung_*`, `consumer_exits_rc0_*` | conforme esperado | aqui | falha de selo por injeção |
| Caller real, runner, targets | forge: AgentEscala `develop@8537eb18`; listagem de workflows de caem/sacr-as | CT104 self-hosted; sem v2 em CAEM/SACR-AS | leitura de fonte | não executado |

## Redesenho do escopo de evidência de recursos (revisão Codex 5332917933 sobre `3f5b086`)

`TestCount != ClaimCompleteness`. O resultado 216/216 é evidência exata das proposições que
exercitou em `34fc575`, mas **não** é qualificação completa de 301S-RES, que passa a
`NOT_QUALIFIED_IN_S0` (requisito preservado; owners sucessores S1-B/S1-C e #320 para
disponibilidade). Os experimentos não foram alterados nem reexecutados.

| Achado | Estabelecido | Material | Disposição |
|---|---|---|---|
| 4117693712: corpos de tree repetidos não são recobrados por ocorrência (o limite de metadados é excedido numa subárvore compartilhada) | sim (leitura de código) | sim, a 301S-RES; sem efeito sobre a autenticidade | `SHARED_TREE_METADATA_PER_OCCURRENCE` → S1-C. **Mesma família de falha de RC-2, mas testemunha fora do Δ1 demonstrado de RC-2: recorrência não admitida** (correção do registro 5861448551) |
| 4117693716: o leitor do cabeçalho pode pré-ler até um bloco (64 KiB) do corpo antes da cobrança | sim (leitura de código) | sim, à evidência de transporte estrito; sem efeito sobre a autenticidade | `TRANSPORT_HEADER_BODY_PREFETCH` → S1-B: admitir o tamanho declarado antes de consumir o corpo além de uma margem de framing estritamente limitada |
| 4117693719: o runner de evidência sai com 0 mesmo quando um experimento falha | sim (leitura de código) | não, ao 216/216 registrado (reconciliado caso a caso) | defeito do harness de evidência → harness de qualificação de S1: falha → código de saída diferente de zero; o runner de S0 fica congelado |

A família sucessora completa (original, RC-2, RC-3, K4, as duas pós-Ready e R4-3) está em CONTRACT §12
(`S1_CLOSURE_RESOURCE_COUNTERMODELS`), cada item com a sua proveniência.

## Reconciliação pós-Ready (revisão Codex 5332770958 sobre `fdf4193`)

`PriorEvidenceRecord != CurrentQualifiedClaim`. Os experimentos não foram alterados nem reexecutados;
os registros de `34fc575` continuam válidos para o que observaram.

```yaml
C9:
  observed: {authorized_alternate_flattened: true, unauthorized_alternate_bytes_admitted: false}
  not_established: {all_producer_filesystem_operations_inside_A: true}
  post_ready_countermodel:
    alternate_root_parent_probe_escape: {established: true, source_head: fdf4193, review: 5332770958, finding: 4117560411}
  successor_owner: S1_A_CORE_PHYSICAL_AUTHORITY      # S1_A_STORAGE_ACCESS_CONTAINMENT, CONTRACT §12
K4:
  remaining_budget_bound: HELD                       # no expansion beyond the remaining node budget
  C3_parity_at_exact_boundary: NOT_HELD              # empty tree refused when the budget is exactly exhausted
  finding: 4117560412
  successor: [S1_SEM_01, S1-C]
lock_dangling_continuation:
  finding: 4117560405
  owner: FUTURE_SD                                   # registered in CONTRACT §7.3 as a mandatory S_D countermodel
```

## Ratificação da arquitetura C (commit documental sobre `34fc575`)

```yaml
experimental_subject: 34fc57562edfcb8f59d3ed0c359dc9bffb95d47d   # 216/216 aqui, runtime declarado; experimentos inalterados desde então
ratification_subject: the docs-only commit that follows 34fc575    # muda só CONTRACT/EVIDENCE/README; nenhum experimento reexecutado
architecture: {A: REJECTED, B: REJECTED, C: RATIFIED_AS_COMPONENT_CONTRACT}
candidate_evidence (bound to 34fc575): {K1: corrected_and_discriminated, K2: corrected_and_discriminated,
                                        K3: corrected_and_discriminated, K4: corrected_and_discriminated,
                                        RC5: corrected_and_discriminated}
applicability: Applicable_SG   # CONTRACT §2; requires AuthorizedReaderExecutionContext, a host/launcher precondition
```

"216/216" vale para o head **experimental** `34fc575`; os commits posteriores (ratificação e reconciliação documental) mudam só documentação.

Achados da revisão do exact head `34fc575` (comentário 5859932046), registrados aqui com a disposição do mantenedor. **Nenhum foi corrigido.**

| Achado | Estabelecido | Disposição | Dono sucessor |
|---|---|---|---|
| **TF1**: vista read-only por bind mount de inodes graváveis por outro mount é aceita pelas checagens do leitor; o runner grava `config` pelo caminho rw e o Git executa o helper dele | reproduzido (revisão adversarial) | `OUTSIDE_RATIFIED_READER_EXECUTION_CONTEXT`; não é recorrência de fronteira; contramodelo obrigatório de ativação | S1/U3 |
| **TF2**: dentro de outro user namespace, ids numéricos `[2000]*4` e caps 0 são aceitos sobre um snapshot do runner; o helper do runner executa | reproduzido (revisão adversarial) | `OUTSIDE_RATIFIED_READER_EXECUTION_CONTEXT`; contramodelo obrigatório de ativação | S1/U3 |
| **X1**: Git setuid/setgid ou com file caps pode ganhar credenciais no `exec` (sem `PR_SET_NO_NEW_PRIVS`); `killpg` → EPERM não é tratado | leitura de código (Codex P1, 4116964963) | `VALID_SUCCESSOR_OWNED`, não corrigido | S1_CTX_01 |
| **X2/TF4**: manifesto de alternates (≤ 64 KiB, truncado em silêncio acima disso) e resolução de ponteiros não são cobrados; 11.903 `open` contra 23 entradas cobradas | reproduzido + Codex P2 (4116964966) | `VALID_SUCCESSOR_OWNED`; K3 cobre só a listagem | S1_RES_01 |
| **TF3**: toda recusa do C3 abaixo da raiz é reportada como `budget_nodes` | reproduzido | causa de recusa errada, decisão certa | S1_SEM_01 |
| **TF5**: exceção entre o spawn do Git e o `try` deixa o Git vivo | reproduzido (por erro de construção) | K2 "qualquer desfecho" vale só depois do `try` | S1_LIFE_01 |
| **TF6**: HEAD, `config`, recibo e subdiretórios sem `fsync`; o recibo cobra objeto deduplicado | reproduzido (dedupe) + leitura de código | visibilidade atômica sim, durabilidade a queda de energia `NOT_QUALIFIED`; redação corrigida, mecanismo não | S1_PUBLISH_01 |
| **R4-3**: pack acima do envelope torna S_G indisponível | reproduzido (revisão de `a858dc9`) | `ACCEPTED_LIMITATION` (disponibilidade, não autenticidade) | S1 + #320 |

**Correções de texto** (sem mudar mecanismo nem experimento). Estas afirmações superestimavam a evidência e ficaram assim:
- "`ReaderPrincipal` = credenciais que o filesystem usa" é agora defesa em profundidade dentro de um `AuthorizedReaderExecutionContext` estabelecido pelo host/launcher.
- "K3: nenhuma enumeração ilimitada precede a limitada" vale **só para a listagem de diretórios**.
- "K2: em qualquer desfecho" vale **depois do `try`**.
- "K4: 0 entradas além do restante" passa a "no máximo restante+1 materializadas pelo parser; o contador mede listas completas".
- "finalize … fsync" passa ao conjunto exato de `fsync`.
- O recibo conta só o que de fato conta.

**Portão de ativação.** S1 de produção **não** pode rodar nos contextos de TF1/TF2: sem o `AuthorizedReaderExecutionContext` estabelecido, S_G não se torna confiável nem consumível (CONTRACT §2, §12).

## Arquitetura C — EXP-ARCH-C (head experimental `34fc575`)

`exp_arch_c.json` (45/45, `evidence: REPRODUCED` em cada caso; 7 casos novos após a revisão de `a858dc9` e 14 no corte terminal — K1–K4, RC-5). O orquestrador roda como root no
container; o produtor (`s0_snapshot_c.py`) roda como uid 0; o leitor (`s0_reader_c.py`) roda
**sempre** como uid 2000 via `setpriv --reuid=2000 --regid=2000 --clear-groups`. A propriedade é
`RunnerCanRead ∧ ¬RunnerCanMutate`; uid 0 é o mecanismo deste container, não a primitiva.

| Discriminador | Casos | Resultado | Limite |
|---|---|---|---|
| **C1** mutação pelo runner | `C1_runner_mutations_denied_by_kernel`, `C1_POSITIVE_runner_reads_snapshot` | 12 syscalls: EACCES (escrever `config`, criar alternates, reescrever/renomear loose, reescrever pack, `unlink`, `rename`, `mkdir`, renomear o snapshot, criar irmão em `committed/`), EPERM (`chmod` do snapshot e de objeto); leitura ok; dono/modo `0:0o555` | DAC deste container |
| **C2** metadata da fonte | `C2_source_metadata_does_not_cross` | fonte com remote, promisor, hooks, alternates, refs: topo do snapshot = `HEAD`, recibo, `config`, `objects`, `refs`; nada da fonte | — |
| **C3/C4** injeção após publicação | `C3_inject_alternates_after_publish_kernel_refused`, `C4_inject_promisor_config_after_publish_kernel_refused` | EACCES (recusa do kernel, não "Git ignora") | — |
| **C4/C8B** objeto ausente | `C4_C8B_missing_required_object_no_network` | `object_missing`; marcador de busca ausente; pré-condição (objeto ausente na fonte) verificada | — |
| **C8A** clone parcial completo | `C8A_partial_clone_complete_closure_accepted` | aceito; marcador ausente; pré-condição `rev-list --missing=print` verificada | Git do fixture roda como o runner |
| **C9** alternates | `C9_authorized_alternate_flattened_no_pointer`, `C9_unauthorized_alternate_typed_refusal` | autorizado: achatado, sem ponteiro, 1 fonte alternativa; fora da capability: `alternate_outside_authorized_storage` (bytes não admitidos) | quem autorizou a capability: #331/C2_B; prova só `ByteAdmissionContainment`: o confinamento de todas as aberturas do produtor **não** foi estabelecido (a sonda abre `..`/`../HEAD` do alternate; achado 4117560411) |
| **C5** bomba | `C5_producer_never_inflates`, `C5_unrelated_bomb_does_not_affect_runner_S_G`, `C5_bomb_in_closure_runner_bounded_refusal` | loose de 256 MiB inflados (261.293 B físicos): produtor VmHWM 21,7 MiB, heap 2,62 MiB; fora da closure → S_G aceito; na closure → `budget_payload_bytes`, unidade Git ≤ 14,8 MiB | envelope por processo; agregado não testado; bomba fora da closure só como **loose** (um pack acima do envelope torna S_G indisponível: F3) |
| **C5 (leitor de C)** orçamento por ocorrência e de paths; listagem do produtor | `C5_blob_charged_per_occurrence_refused`, `C5_POSITIVE_repeated_blob_within_budget_accepted`, `C5_path_bytes_budget_enforced_during_walk`, `C5_producer_listing_charged_to_entry_budget` | 1 MiB × 100 paths com 8 MiB → `budget_payload_bytes` (controle 4 paths aceito); ~18,6 MB de paths → `budget_path_bytes`; 1.200 nomes não-objeto com 1.000 entradas → `physical_budget_exceeded` | o mutante é `a858dc9`, que aceitou os três (reprodução da revisão) |
| **C6** autoridade da raiz (`aux_record_no_effect` vale **por construção**: `commit_sg` não recebe raiz; discriminam a ablação e o mapa forjado) | `C6_root_derived_from_authenticated_commit_only`, `C6_ABLATION_trusting_aux_root_changes_S_G`, `C6_forged_object_map_entry_refused` | raiz `a3ab46d8…` derivada de `C`; registro auxiliar sem efeito; ablação `root_override` → raiz `6a638b87…` (S_G muda); mapa forjado → `object_map_binding_mismatch` | integridade do processo leitor: **P** |
| **C7** tempo de vida da unidade | `C7_runner_unit_teardown_0_survivors`, `C7_ABLATION_process_group_only_leaves_setsid_survivor` | ferramenta falsa → filho → neto `setsid` → `sleep`: `transport_deadline`, 3 mortos, 0 restantes, leitor uid 2000; ablação: 1 sobrevivente, limpo por pid + starttime + nonce | — |
| **C10** crash antes do commit point | `C10_sigkill_before_publish_no_committed_snapshot` | SIGKILL no staging: nenhum snapshot novo em `committed/`; lixo de staging presente; runner lista `staging/` → EACCES | GC do staging: futuro |
| **C11** leitor como o runner | `C11_unprivileged_reader_derives_S_G_structure`, `C11_COUNTER_reader_refuses_runner_mutable_snapshot`, `C11_COUNTER_symlinked_path_into_runner_owned_parent_refused`, `C11_COUNTER_runner_owned_real_ancestor_refused`, `C11_COUNTER_relative_path_refused`, `C11_expected_format_mismatch_refused`, `C11_POSITIVE_real_toolrepo_runner_derived_S_G_equals_prior_record` | uid 2000 deriva o mapa declarado (vazio, executável, symlink `pkg/../main.py`, aninhado); cópia de posse do runner → `snapshot_mutable_by_reader`; snapshot root-owned sob diretório do runner: por symlink root-owned → `snapshot_path_not_canonical` (contramodelo confirmado: o runner renomeia o snapshot), por caminho direto → `snapshot_mutable_by_reader`, relativo → `snapshot_path_not_canonical`; formato divergente → `snapshot_format_mismatch`; toolrepo real → `95504743…` | igualdade do digest é controle positivo **neste** subject; o mutante da checagem lexical é `a858dc9` |
| Positivos | `POSITIVE_sha256_runner_S_G`, C9 autorizado, C8A, C11 | sha256 aceito; sha1 no corpus real | — |
| Lifecycle | `LIFECYCLE_no_survivors_no_listeners` | sem processos nem sockets em escuta | namespace de PID do container |

**Vetor de recursos (`resource_vector`, dois domínios):**

```yaml
physical_snapshot: {compressed_bytes: 3646095, entries: 2, alternate_depth: 0, producer_heap_MiB: 3.81,
                    producer_vmhwm_MiB: 23.3, io: {rchar: 5632037, wchar: 3646583}, time_s: 0.061, producer_uid: 0}
subject_closure:   {unique_objects: 933, object_reads: 933, nodes: 1001, payload_bytes_charged_per_occurrence: 8660242,
                    metadata_bytes: 51346, path_bytes: 54726, reader_heap_MiB: 36.69, reader_vmhwm_MiB: 58.2,
                    local_git_processes: 1, per_object_deadline_s: 30, time_s: 0.295, reader_uid: 2000}
local_git_unit:    {rss_MiB_upper_bound: 14.8, envelope: "RLIMIT_AS 128 MiB per process", aggregate_memory: NOT_TESTED}
```

**Classes de evidência:** OBSERVED — ambiente, privilégio do container; REPRODUCED — C1–C11,
positivos, lifecycle; INFERRED — a necessidade do envelope no C5 vem da ablação de EXP-RES (filho
sem contenção 68 MiB); aqui só o lado contido foi medido; NOT_TESTED — ver abaixo.

**B-findings sob C (disposição válida só dentro de `Applicable_SG`, CONTRACT §2):** B1/B2
`ELIMINATED_BY_PRIVILEGE_BOUNDARY` (C1/C3/C4/C8), B3 `ELIMINATED_FROM_PHYSICAL_PRODUCER` (C5), B4
`AUTHORITY_REDUCED_TO_AUTHENTICATED_COMMIT` (C6), B5 `DISCRIMINATED_BY_DESCENDANT_SURVIVOR_CONTROL`
(C7), B6 `PARTIAL` (`tmp_obj_*` ignorado pelo nome; o resto não retestado). Nota: a revisão de `a858dc9` reproduziu o
mecanismo de B-2 por um caminho **não canônico** entregue ao leitor (RC-1), e TF1/TF2 o reproduzem
fora do contexto autorizado; por isso a eliminação de B1/B2 vale dentro do
`AuthorizedReaderExecutionContext`, não por causa das checagens do leitor.

| Item de A/B | Estado sob C |
|---|---|
| R4-1 mecanismo original | eliminado por construção (o Git nunca lê o repositório vivo) |
| R4-1 família (recursos do Git local) | **aberta**, owner #320; prazo por objeto, `RLIMIT_AS` por processo, subreaper; agregado e prazo total não reivindicados |
| R4-2 / B-4 | raiz derivada dos bytes autenticados de `C`, sem autoridade independente; discriminado por C6 |
| R4-3 | aberto (parâmetro do envelope) |

## Corte terminal da arquitetura C (`34fc575`): K1–K4, RC-5, R4-3

Grant do mantenedor ("ARCHITECTURE C — TERMINAL CONTRACT CLOSURE CUT"). Subject revalidado antes de qualquer mutação: head `28a3b4a`, base `9abcde6`, sem drift. É **um** commit corretivo. Depois dele vale `NO_AUTOMATIC_PATCH_LOOP`: achado material novo vai para adjudicação humana.

**Disposição do mantenedor para RC-1:** `LOCAL_ENFORCEMENT_DEFECT_OF_READER_APPLICABILITY`, `boundary_recurrence: false`; na época condicionada ao `ReaderPrincipal` (K1) e, sob a ratificação posterior de S0-C, ao `AuthorizedReaderExecutionContext` (as checagens K1 do leitor passam a ser defesa em profundidade). Ver CONTRACT, topo.

| Item | Correção | Discriminadores (`exp_arch_c.json`, `34fc575`) | Resultado | Limite |
|---|---|---|---|---|
| **K1** — o principal do leitor era autodeclarado (`os.getuid()`, `access` pelo id real) | `ReaderPrincipal` lido de `/proc/self/status` e comparado ao runner **esperado**. Exige uid e gid real = efetivo = salvo = filesystem, e `CapEff`, `CapPrm`, `CapInh`, `CapAmb` = 0. Escrita checada com credenciais **efetivas** (`effective_ids=True`). | `K1_POSITIVE_established_runner_principal_accepted`, `K1A_real_runner_effective_snapshot_owner_refused`, `K1B_saved_uid_of_snapshot_owner_refused`, `K1C_permitted_capability_with_CapEff_0_refused` | **Positivo:** uid/gid 2000 nos 4 campos, caps 0 → aceito sobre um snapshot do produtor não-root uid 3000. **K1A:** `Uid 2000 3000 3000 3000` → `reader_principal_mismatch`. **K1B:** `Uid 2000 2000 3000 2000` → `reader_principal_mismatch`. **K1C:** `CapPrm a80425fb`, `CapEff 0` → `reader_has_capabilities`. Em K1A/K1B/K1C: **contramodelo exercitado** (o processo altera o snapshot com o que possui; em K1C, via `capset`) e a checagem de `28a3b4a` o aceitava. | K1A/K1B/K1C constroem as credenciais **no próprio processo** (setresuid/KEEPCAPS e depois `runpy` do leitor), porque o `exec` iguala o uid salvo ao efetivo. Grupos suplementares só registrados. |
| **K2** — sobrevivente ignorado quando a captura já falhava | Qualquer descendente restante → `unit_teardown_incomplete` em qualquer desfecho **depois do `try`** (a janela entre spawn e `try` é TF5). A falha primária fica como diagnóstico. | `K2_failure_path_with_surviving_descendant_refused_as_teardown_incomplete`, `K2_ABLATION_success_only_rule_hides_the_survivor` | Transporte que falha + teardown que reporta sobrevivente → `unit_teardown_incomplete`, primária `transport_header_invalid`. A unidade real termina vazia. A ablação devolve `transport_header_invalid` e esconde o sobrevivente. | `real_D_state: NOT_TESTED`: o teardown é um stub. C7 (filho + neto `setsid` reais) preservado. |
| **K3** — sonda de alternate da G1C enumerava sem limite antes da caminhada limitada | Sonda experimental com a mesma semântica: `HEAD` irmão, `pack/` e `info/` em O(1); a busca de fanout passa por `scanned()`. **A G1C de produção não foi alterada.** | `K3_alternate_probe_charged_to_entry_budget`, `K3_ABLATION_unbounded_G1C_probe_enumerates_beyond_budget`, `K3_POSITIVE_small_standalone_pool_accepted` | 3.000 entradas-lixo antes do fanout, orçamento 1.000 → `physical_budget_exceeded` com 1.001 entradas enumeradas no processo. Ablação (sonda da G1C): 4.002. Pool pequeno aceito pelo produtor e pelo leitor. Cobre só a **listagem**; manifesto e resolução de ponteiros não são cobrados (X2/TF4). | Precondição registrada: fanout na posição 3.000 da listagem. S1 exige uma sonda limitada aprovada pelo owner. |
| **K4** — parser de tree com teto global | Teto = **restante** do orçamento de nós; restante ≤ 0 recusa antes de carregar a tree. | `K4_tree_parser_bounded_by_remaining_node_budget`, `K4_ABLATION_global_cap_materializes_beyond_remaining`, `K4_POSITIVE_same_shape_within_remaining_budget_accepted` | 992 nós consumidos, `max_nodes` 1.000, tree compacta de 50 entradas → `budget_nodes`, teto 8; o parser materializa no máximo restante+1 antes de recusar (o contador conta listas completas: 0). Ablação: 50 materializadas. Com 5 entradas: aceito. | O leitor converte **toda** recusa do C3 abaixo da raiz em `budget_nodes` (TF3; S1_SEM_01). O limite de recurso se mantém; a paridade com o C3 na fronteira exata não (tree vazia com orçamento exatamente esgotado recusada; achado 4117560412). |
| **RC-5** — escrita curta | Laço de escrita (em `28a3b4a`), agora com **discriminador causal** | `RC5_forced_short_writes_publish_exact_bytes`, `RC5_ABLATION_one_write_per_chunk_publishes_mismatch` | `write` forçado a ≤ 4.093 B: os 10 arquivos publicados são iguais à fonte, o recibo é igual ao recalculado do destino e o leitor aceita. Ablação "uma escrita por bloco": bytes e recibo divergem. | Escritas curtas forçadas por wrapper sobre o `os.write` real, não por quota de disco. |
| **R4-3 / RC-6** — pack acima do envelope | Nenhuma correção nesta rodada. | — (reproduzido pela revisão de `a858dc9`) | `ACCEPTED_S0_LIMITATION_REQUIRES_S1_PARAMETER_DECISION`: afeta disponibilidade, não autenticidade nem a identidade de S0. | Nenhum número novo inventado. |

**Suíte do head experimental `34fc575`:** 216/216 (10 scripts; `exp_arch_c` 45/45). Os scripts foram hasheados **antes** da execução registrada e conferidos depois. Três ensaios não registrados antecederam a execução:
- 1º: `KeyError` no runner (`os.walk` usa `scandir`, e o proxy de contagem não era iterador);
- 2º: `NameError` ao reintroduzir o proxy, que removeu a classe `Refused` do produtor; além disso, no tmpfs deste kernel a listagem sai da mais nova para a mais antiga, e a fixture de K3 pôs o fanout antes do lixo, invalidando a ablação;
- 3º: 216/216.

Esses defeitos de harness foram corrigidos **sem** mudar expectativas.

**Host 3.12** (fora do runtime declarado): na revisão de `a858dc9`, `tests/agent_review` teve 3 falhas. Duas também falham na base limpa neste host (registrado antes). A terceira, `test_install_script_produces_a_working_minimal_venv`, falhou por hash do pip. Ela é **correlacionada ao ambiente e fora do write-set**, mas **não foi comprovada na base** (a base não foi reexecutada). O CI 3.11 verde é a evidência principal do runtime declarado.

## Revisão do exact head `a858dc9` → correção proporcional única (`28a3b4a`)

Revisores do exact head `a858dc9`:
- **Codex:** review 5331717758, com 5 comentários inline.
- **Revisão adversarial independente:** subagente com contexto limpo, do mesmo modelo, portanto com independência limitada. Os experimentos rodaram em containers descartáveis.

CI do head `a858dc9`: verde. O comentário 5859018189 registra o corte. Ambos os revisores atacaram as prioridades 1–8. Nada forjado passou pelo hash-on-read, e a separação de privilégio é real: o leitor roda com uid/gid 2000 e todas as capabilities zeradas.

| # | Achado | Fonte | Estabelecido | Material a | Classificação (regra de convergência) | Destino |
|---|---|---|---|---|---|---|
| RC-1 | A checagem de imutabilidade do leitor era **lexical**. Com um symlink root-owned no caminho apontando para um diretório do runner, ou um caminho relativo, o snapshot era aceito; depois disso o runner trocava o diretório, e o Git honrava a `config` do runner e **executava o helper dele** | Codex P1 (4116643939); adversarial F1 | **reproduzido**: aceito + `RENAMED_AND_RESTORED`; troca instrumentada com o helper executado; corrida real: 38/80 aceitos e 3/80 com o helper executado | 301S-PRIV (checagem no consumo); disposição candidata de B2 | O revisor adversarial classificou como **(a) condicional ao caminho**. Adjudicação: **defeito local da precondição**. Com o caminho canônico de publicação, os dois revisores confirmam que o runner não tem autoridade de escrita sobre nada que o Git lê. A autoridade explorada (ancestral real do runner) já era excluída pelo contrato ("qualquer ancestral; symlink"); a implementação da checagem não a verificava. **Decisão sensível — ver a adjudicação na PR.** | caminho absoluto canônico, sem symlink em nenhum componente, componentes reais de `/` para baixo, `CapEff == 0`; três contra-controles C11 novos |
| RC-2 | O leitor de C cobrava o payload **por objeto único**, mas materializava por ocorrência | Codex P1 (4116643927); adversarial F2 | **reproduzido**: 1 MiB × 100 com orçamento de 8 MiB → aceito, 104,9 MB selados; 200 × 1 MiB → VmHWM 629,6 MiB | 301S-RES (`closure_budget`) | Transição cobrar-X/consumir-Y **de recurso**; a autenticação continua intacta. É regressão de uma propriedade que A tinha discriminado (EXP-RES) e que o port para C não levou; EVIDENCE de `a858dc9` citava EXP-RES para C **sem reexecução** (overclaim meu) | cobrança de cada ocorrência repetida; `C5_blob_charged_per_occurrence_refused` + positivo |
| RC-3 | `walk` não chamava `charge_node`: o orçamento de 16 MiB de paths não era aplicado | Codex P1 (4116643928); adversarial F2 | **reproduzido**: 18,6 MB de paths aceitos | 301S-RES | local | `charge_node` na caminhada; `C5_path_bytes_budget_enforced_during_walk` |
| RC-4 | `remaining` do teardown era descartado; o status de subreaper não era conferido | Codex P2 (4116643930); adversarial F5 | leitura de código | 301S-LIFE | local | `unit_teardown_incomplete`, `subreaper_required`; descendente em estado D **não testado** |
| RC-5 | escrita curta do produtor registrada como completa | Codex P2 (4116643935) | leitura de código | recibo (rastreabilidade) | local | laço de escrita; **não testado** |
| RC-6 | um pack não relacionado maior que o envelope torna S_G indisponível | adversarial F3 | reproduzido pelo revisor | disponibilidade; R4-3 | fora das duas classes de STOP; decisão de valores | **não corrigido**: §8 e §12 (i) registram a incoerência 256 MiB × 128 MiB e a razão de recusa enganosa |
| RC-7 | a listagem do produtor não tinha limite (300.000 nomes `tmp_obj_*` aceitos) | adversarial F4 | reproduzido pelo revisor | `snapshot_budget` | local | toda entrada listada conta; `C5_producer_listing_charged_to_entry_budget` |
| RC-8 | discriminadores fracos: C11 só exercitava a posse; `aux_record_no_effect` vale por construção | adversarial F6 | leitura de código | evidência | — | contra-controles C11 novos; C6 rotulado |
| RC-9 | dono de terceiro uid; CAP_FOWNER | adversarial F7 | plausível | 301S-PRIV | — | `CapEff == 0` exigido; terceiro uid fica fora do domínio (§3: "UID diferente: fora"); vincular o dono à identidade do produtor é obrigação de S1/U3 |

**Regra de convergência deste corte.**
- Nenhum achado mostra, **com a publicação que a arquitetura especifica**, outra autoridade gravável sobre o namespace Git.
- Nenhum achado mostra outra transição autenticar-X/consumir-Y na autenticação dos objetos.

RC-1 e RC-2 são as decisões sensíveis:
- **RC-1** reproduz o mecanismo de B-2 quando o caminho entregue ao leitor não é canônico.
- **RC-2** é uma transição cobrar/consumir no orçamento.

Adjudiquei os dois como defeitos locais de obrigações delimitadas (301S-PRIV e 301S-RES) e apliquei **uma** correção proporcional, com requalificação completa. Se o mantenedor entender RC-1 como recorrência da fronteira, a disposição correta passa a ser `STOP_301_C_BOUNDARY_RECURRENCE`. Não haverá outro patch neste corte: achados sobre o head corretivo vão para adjudicação humana.

## Herdado e não reexecutado

- Spike EXP-Q1 (permissões/FD mantido contra uma época C3 viva): citado apenas como motivação
  (PR352#5848970869).
- Censo do spike de que não existe caller de produção de C3/G1: não refeito como busca.
- Registros de #324/#353 (AR-C3-C4Q-KD-20260926-R1): usados como índice.

## NOT_TESTED (e consequência)

- CT104: versão/ABI/ownership do interpretador, Yama, provisionamento root → U2/U3 continuam premissas.
- Runners hospedados com `setup-python` → piso 2 presumivelmente não atendido; não observado.
- Objetos em pack, alternates, repositório parcial → autenticação por argumento (C8A/C9 exercitam
  aceitação, não adulteração dentro de pack).
- **Arquitetura C:** CT104; serviço, identidade e proveniência do produtor em produção (U3);
  proveniência da política do host / C2_B (#331); proveniência do anchor (#319); memória agregada
  da unidade Git sob cgroup; prazo total da captura; GC do lixo de staging em produção; R4-3
  (envelope × packs mapeados); `select` com fd ≥ 1024 (B-6); forja de pack/`.idx` e pack
  corrompido **pelo leitor de C** (reexecutados só pelo código de B).
- memfd sob limite de memcg/OOM; outros kernels.
- Sinais de mesmo UID contra produtor/launcher/filho; integridade de processo do produtor e do launcher.
- Execução de programas por configuração do git durante `cat-file`.
- Modo Router conectado; relançamento de brokers; threads no bootstrap.
- Interpretador não oficial (Debian `/usr/bin/python3.11`), musl.
- Reprodução independente por outro operador/máquina.

## Arquitetura A rejeitada após a rodada 4 → Spike B → arquitetura B (`8c4842b`, depois REJEITADA)

Rodada 4 em `cf69fbf`: duas recorrências admitidas **dentro** do mecanismo ratificado. R4-1: o
transporte `git` é uma árvore de processos (busca lazy de clone parcial antes do cabeçalho). R4-2:
a identidade da raiz fora do registro de aquisição. Pela regra de parada acordada, não houve rodada
5: a arquitetura A (transporte Git vivo) foi **rejeitada** (comentário 5852664959). O mantenedor
escolheu a opção 2. O Spike B foi favorável às 7 perguntas (comentário 5852938161), com as
qualificações do topo de CONTRACT. `8c4842b` foi o corte de B; a revisão de B (comentário
5853311835) estabeleceu recorrência (B-1/B-2) e rejeitou B. A tabela abaixo é o estado **em
`8c4842b`**, preservado; o estado sob C está na seção anterior.

| Item | Estado em `8c4842b` (histórico) |
|---|---|
| R4-1 mecanismo original (busca lazy / árvore de processos do repo vivo) | **eliminado por construção** (SNAP Q1/Q2) |
| R4-1 família (CPU/memória/tempo do Git local sobre o snapshot) | **aberta**, owner #320; prazo, `RLIMIT_AS` e grupo de processos no protótipo; o Spike B e EXP-SNAP reproduziram uma instância de travamento sem prazo |
| R4-2 | mecanismo **integrado e discriminado** (SNAP Q4); fechamento só após revisão do head exato |
| R4-3 (envelope × packs mapeados) | aberto como parâmetro do envelope |
| R4-4 (discriminador B) | corrigido: ablação close-and-wait |
| R4-5 (sessão sem prazo) | prazo por objeto + ablação |
| R4-6 (interface C3 subdeclarada) | preservado; §12 nomeia a decisão do owner |
| `verify-pack` | fora do caminho de S_G; forja de pack/`.idx` sem falso positivo |
| Harness | censo de órfãos incluído (lição dos `git-daemon` órfãos da revisão da rodada 4) |

## Rodada de revisão 3 (head `1299b00`) → STOP/REDESIGN → decisões (a)–(d) → `cf69fbf`

A autenticação de S_G se manteve. Foi admitida recorrência sobre a correção da rodada 1 (limite de
corpos commit/tree). R3-1: o filho `git` expande; medido em 68,2 MiB. R3-2: um cabeçalho negativo
anula a cobrança; reproduzido em 128 MiB com orçamento de 8 MiB. A pergunta 2 do preflight disparou
(duas correções derrotadas: R2-1 e R3-1/2). Registro na PR #355, comentário 5852389024. O mantenedor
decidiu (a)–(d) (topo de CONTRACT). `cf69fbf` foi o corte corretivo único.

| Achado | Destino em `cf69fbf` |
|---|---|
| R3-1 filho `git` expande | transporte contido por `RLIMIT_AS` antes do `exec` + kill/reap na recusa; discriminador A |
| R3-2 cabeçalho negativo/injetado | parse estrito e `readline` limitado; discriminador B |
| R3-3 limite de componente fixo | parâmetro explícito de admissão; paridade condicional; discriminador D |
| R3-4 carrier do C3 relê spool | proposta retirada; aquisição ≠ interpretação; revalidação pós-selo; discriminador C |
| R3-5 números antigos no §8 | §8 atualizado a partir de `cf69fbf` |
| R3-6 piso sem stdlib por arquivo | **preservado** (evidência de E, não S_G) |
| R3-7 resíduos de redação; parse levanta exceções não tipadas; paridade C3 só sintética | preservados como menores; paridade no corpus real passou a ser aceite de S1 |
| R3-Q1 SHA-1 sem sha1dc | premissa de segunda pré-imagem declarada; sha1dc não alegado; discriminador E |

## Rodada de revisão 2 (head `1e2453e`) → STOP/REDESIGN → decisão → correções em `1299b00`

Codex (review 5328675140) trouxe 8 achados, todos estabelecidos. R2-1 foi reproduzido e **admitido
como recorrência** da correção do zip bomb da rodada 1, o que estabeleceu STOP/REDESIGN. O patching
foi congelado, um spike descartável selecionou o mecanismo e as perguntas de redesenho apontaram que
S0 afirmava mais sobre S_D do que o objetivo exige. Registro completo na PR #355, comentário
5852251027. O mantenedor aceitou a redução (topo de CONTRACT).

| Achado | Destino | Correção / preservação |
|---|---|---|
| R2-1 metadados inflados antes da cobrança | S_D (futura) | contramodelo obrigatório; `repro_r2.py` |
| R2-2 arquivo do wheel sem limite | S_D (futura) | contramodelo obrigatório; spike |
| R2-3 `DT_FILTER`/`DT_AUXILIARY` | S_D (futura) | contramodelo obrigatório |
| R2-4 algoritmo do cabeçalho ignorado | **S_G — corrigido** | consumidor exige o algoritmo implicado pelo `C` esperado; `binding_right_commit_wrong_algorithm_label` |
| R2-5 aberturas relativas escapam da auditoria | **evidência — corrigido** | caminho resolvido + comparação por componente; `auditor_detects_relative_checkout_read` |
| R2-6 probe do piso testa o pai | **evidência — corrigido** | probe por arquivo; `floor_probe_detects_writable_file_in_readonly_dir` |
| R2-7 pacote-extensão não procurado | S_D/loader (futura) | contramodelo obrigatório |
| R2-8 superconjunto de tags aceito | S_D (futura) | contramodelo obrigatório; `repro_r2.py` |

## Rodada de revisão 1 (head `3d426e1`) → correções em `1e2453e`

Revisores: Codex (`chatgpt-codex-connector`, 8 comentários inline no head exato) e um subagente de
revisão adversarial com contexto limpo (mesmo modelo; independência limitada). Todos os achados foram
reproduzidos ou confirmados por leitura de código; a adjudicação e as respostas às três perguntas de
recorrência estão no corpo da PR.

| Achado | Materialidade | Correção | Evidência nova |
|---|---|---|---|
| Corpos de commit/tree lidos sem limite (Codex P1; revisão F1) | material a 301S-RES | cobrança pelo header para commit/tree; limite pré-leitura do C3 | `oversized_tree_*`, `tree_bytes_*`, `oversized_commit_*` |
| Zip bomb inflado antes do limite de S_D (Codex P1) | material a 301S-RES | `ZipInfo.file_size` antes de ler | `compressed_member_refused_before_inflate` |
| Lock como symlink autorizaria S_D (Codex P1) | material a 301S-DEP | exigir nó `regular` no produtor e no consumidor | `lock_bytes()`; checagem no bootstrap |
| Nativo com RPATH/`DT_NEEDED` absoluto (Codex P1) | material a 301S-NAT | inspeção ELF | `native_*` |
| Identidade do wheel pelo nome do arquivo (Codex P1; revisão Q5) | material a 301S-DEP | `.dist-info`, `METADATA`, `WHEEL`, tags vs interpretador; tag no cabeçalho de D | `wheel_renamed_*`, `wheel_tag_*`, `wheel_other_interpreter_abi` |
| Colisão arquivo/diretório (Codex P2) | material a 301S-DEP | recusa | `file_directory_prefix_collision_one_wheel` |
| Produtor importava C3 do checkout gravável (Codex P2; revisão F4) | material ao piso de TCB | argumento `producer_src` root-owned; `-B` | configuração do runner |
| Socketpair vazava em falha pré-spawn (Codex P2) | material a 301S-LIFE | posse dos dois lados até o spawn | `launcher_pre_spawn_*` |
| Finder ignorava `path` (revisão F3) | material a 301S-LOAD | marcador por finder; ordem como `FileFinder` | `D_cannot_extend_S_or_stdlib_packages` |
| Regras do C3 copiadas com valores diferentes; duplicado já é do C3 (revisão F2) | material ao §11.2 | valores alinhados (profundidade 100), texto corrigido, S1 consome o builder do C3 | — |
| Nenhuma recusa de S_D exercitada (revisão F5) | material à evidência | `exp_deps.py` | 25 casos |
| `inspect`/`importlib.resources`/`pkgutil` implícitos (F6) | menor | declarados não suportados | — |
| Números rotulados de forma imprecisa (F7) | menor | contagens medidas ou rotuladas "por construção" | `resources` |
| Piso checava 4 caminhos (F8) | menor | 23 caminhos | `floor` |
| Limite do audit hook (F9) | menor | mais eventos; limite declarado | — |
| Vínculo S_D↔lock é de rótulo (F10) | menor | texto | — |
| `parse_lock` divergia do pip (F11) | menor | duplicado/marker/comentário recusados | `lock_*` |
| `pwd` não atribuído (Q2) | menor | pilha registrada | `census.E_import_pwd_stack` |
| Premissa **P** do produtor/launcher; sinais; git filho (Q1, Q4) | menor | tabela de atores | — |
| U3 afeta S1; decisões de orçamento e do owner C3 (F4, Q3) | material ao handoff | precondição e decisões explícitas em §12 | — |

## Defeitos do harness corrigidos durante a construção

Nenhum mudou uma expectativa para acomodar resultado da engine ou do kernel, salvo onde dito.

1. Canal socket testado no número de FD errado → número herdado real.
2. Marcador residual creditado ao caso seguinte → limpeza antes de cada execução.
3. Payload de startup usava `open` antes de existir (o código **executou**) → `posix.open`.
4. Caso de orçamento de nós media outro mecanismo → fixtures separadas; na rodada 2 o mecanismo
   observado passou a ser o cap do C3 alimentado com o orçamento restante, e a expectativa registra
   esse mecanismo (a mudança veio da correção de 301S-RES, não do resultado).
5. Bundle inacessível ao usuário 2000; fallback que criaria ref no checkout de origem substituído.
6. C3 recusou overlayfs como workspace → `/work` em tmpfs (domínio admitido de C3).
7. Fixture ELF com `DT_NEEDED` absoluto não tinha a dependência (linker `--as-needed`) →
   `--no-as-needed` e verificação do próprio fixture.
8. **Sobre-recusa detectada pelo corpus positivo**: a checagem de tags não expandia a tag comprimida
   que o maturin grava no `WHEEL` do `pydantic_core` → expansão igual à do nome do arquivo.
9. Linha vazia em `/proc/self/maps`; `ldconfig` fora do PATH do usuário → caminho absoluto.
10. (Spike C) `ru_maxrss` herda o RSS pré-`exec` do pai (533,7 MiB idêntico em todo subprocesso) →
    VmHWM de `/proc/self/status`; o valor contaminado ficou registrado no comentário do spike.
11. (Spike C) Git do fixture C8A rodando como root sobre repositório do runner foi recusado pela
    checagem de ownership (o transporte local limpa `GIT_CONFIG_*`) → closure incompleta, recusada
    corretamente pela fronteira. Corrigido: Git do fixture como o runner + pré-condição
    `rev-list --missing=print` verificada antes do caso.
12. (Corte `a858dc9`) `run_py311.sh` quebrava num apóstrofo de comentário dentro de `bash -c '…'` →
    comentário reescrito.
13. (Corte `a858dc9`) **Erro meu de contagem** em C11: a expectativa estrutural foi escrita como 9 nós; a
    árvore declarada tem 8. A primeira execução falhou só nesse caso (preservada no scratch, não
    registrada). A expectativa passou a ser o mapa **declarado** caminho → tipo mais o target do
    symlink, escrito antes da nova execução; o resultado do leitor não foi copiado para a
    expectativa.
14. (Corte `a858dc9`; mudança de mecanismo, não de expectativa) o Spike C continha a unidade Git num PID
    namespace criado com privilégio. O leitor de C roda como o runner, que não cria user/PID
    namespace sob o seccomp padrão do Docker; exigir isso seria dependência de privilégio (C11). A
    contenção passou a ser *child subreaper* + teardown da subárvore, com a mesma ablação (grupo de
    processos) e o mesmo critério de 0 sobreviventes.
