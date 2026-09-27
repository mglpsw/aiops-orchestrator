# #301 S0 — índice de evidência

Índice das afirmações materiais de [`CONTRACT.md`](CONTRACT.md). **Não é norma**: aponta para o
contrato e para os artefatos; não o repete. Hashes provam integridade dos arquivos, não autoridade
nem suficiência.

## Subject e ambiente da execução registrada

```yaml
engine_subject: {commit: 9abcde6420a59b814b5faaff10ca5904c5d23370, tree: 93143d70ed410771776f5f2cdb48d7f8e3f5ed9b}
runtime: {image: "python:3.11-bookworm@sha256:b99029c95d3d37fb1e4e76d287f7984373dca77c665885986e31b2c95260c13c",
          python: 3.11.16, git: 2.39.5, glibc: 2.36, kernel: 6.18.33.2-microsoft-standard-WSL2,
          work_fs: tmpfs, yama_ptrace_scope: 1, actor_uid: 2000, tcb_owner: root}
wheels: iguais aos sha256 do lock (environment.json)
command: bash experiments/run_py311.sh <checkout> 9abcde6420a59b814b5faaff10ca5904c5d23370 <results>
results: experiments/results/py311-20260926/
scripts: experiments/results/py311-20260926/SCRIPTS.sha256
outcome: 93/93 casos com expectativa passaram; 0 stderr; 2 observações sem expectativa (exp_process_channel)
reproduction: própria (esta sessão, 2026-09-26); não é reprodução independente
```

Os valores esperados estão escritos em cada script antes da execução registrada. Durante a
construção houve execuções de ensaio em CPython 3.12.3 no host; elas **não** são evidência
registrada, e os defeitos de harness que revelaram estão listados abaixo.

## Afirmações → evidência

| Afirmação (CONTRACT) | Arquivo / casos | Resultado | Origem | Limite |
|---|---|---|---|---|
| `git cat-file` serve bytes que não hasheiam ao oid (rc 0) | `exp_n1_auth.json` `*_HOR_git_itself_serves_swapped_blob_rc0` | sha1 e sha256: sim | reproduzida aqui (spike HOR herdado, 3.12) | objetos loose |
| Adulteração de commit/tree/blob recusada pelo mismatch do objeto certo | `*_blob_swapped_*`, `*_tree_swapped`, `*_commit_swapped` | `object_hash_mismatch/{blob,tree,commit}` | aqui | loose; pack/alternates só por argumento |
| Discriminador é o hash, não falha incidental | `*_ABLATION_verify_disabled_*` | mutante aceita e incorpora `EVIL` | aqui | — |
| Hash de uma leitura + cópia de outra reabre a janela | `MUTANT_verify_then_reread_embeds_other_bytes` | bytes diferentes | aqui | — |
| Formato do objeto vem do `C` esperado | `expected_sha256_id_against_sha1_repo`, casos `sha256_*` | `object_format_mismatch`; sha256 completo aceito | aqui | git 2.39 |
| N1 contra G1/Q no master integrado | `INHERITED_N1_G1_Q_accepts_EVIL_after_private_copy_swap` | `SUCCESS` para `EVIL` | **reexecutada** (spike PR352#5848970869, 3.12, base 9a5cf35) | viola a quiescência de Q; testemunha para S, não defeito de Q |
| Fidelidade A/A2: paridade com árvore declarada e com C3 | `exp_structure.json` `parity_*` | igualdade exata | aqui (spike EXP-S1 herdado) | fixture sintética |
| 7 distinções materiais preservadas; mutante com perda colide | `countermodel_*` | 7/7 distinguidos; mutante colide 5/7 | aqui | — |
| Recusas explícitas | `gitlink_refused`, `noncanonical_mode_100664_refused`, `dotdot_name_refused_by_C3_rule`, `duplicate_name_refused` | recusados | aqui | duplicado: regra no protótipo, deve migrar para C3 |
| Nome não-UTF-8 | `non_utf8_name_S_vs_C3` | S e C3 aceitam (C3 via surrogateescape) | aqui | observação |
| Pós-compromisso: 9 operações de escrita negadas | `exp_capture_stability.json` `sealed_attack_battery` | EPERM; `mprotect` EACCES; `MAP_PRIVATE` só COW; `F_GET_SEALS=0xf` | aqui (spike EXP-S1 parcial herdado) | kernel 6.18 |
| Janela pré-selo detectada; ablação compromete adulterado | `pre_seal_write_detected_at_commit`, `ABLATION_no_post_seal_rehash_*` | `sealed_content_mismatch`; mutante `COMMITTED` | aqui | — |
| Selo estranho / mapeamento gravável retido | `pre_seal_foreign_*` | `seal_failed` | aqui | — |
| M mutado após captura não altera S; consumidor não lê M | `M_mutated_after_capture_S_unchanged`, `consumer_imports_trusted_bytes_from_S`, `consumer_opens_under_M` | digest igual; `trusted`/`NamespaceLoader`/`ModuleNotFoundError`; 0 aberturas | aqui (spike EXP-S2/S3 herdados) | `cwd=M` e `PYTHONPATH=M` oferecidos |
| Substituição de binding recusada antes do driver | `binding_*` (6) | cada razão esperada; driver não executou | aqui | — |
| Pipe de resultado é forjável; socketpair não | `exp_process_channel.json` | `INJECTED`/`FORGED`; `ENXIO`/`genuine` | aqui | — |
| Não-ancestral mesmo UID: sem attach/`/proc/pid/mem`; lista FDs se dumpable | `same_uid_non_ancestor_vs_*` | observado | aqui | Yama=1 neste host |
| `pyvenv.cfg` editado executa código antes do `-c` sob `-I -S` | `exp_bootstrap_env.json` `venv_python_I_S_same_uid_edited_pyvenv_cfg` | payload executou | **reexecutada** (spike EXP-F1, 3.12) | stdlib copiado para prefixo do atacante |
| `LD_PRELOAD` age sob `-I -S`; `env={}` fecha | `LD_PRELOAD_with_I_S`, `empty_environment_I_S` | sim / não | aqui | — |
| `-I` e `-S` fecham portas diferentes | matriz `S_only_*`, `E_S_*`, `I_only_*`, `I_S_*`, `no_flags_*` | conforme esperado | aqui | — |
| Piso: interpretador/stdlib root-owned, não graváveis pelo ator | `floor` | uid 0, escrita negada | aqui | **só neste container**; CT104 não observado |
| Engine real executa só de S_G/S_D com paridade | `exp_functional.json` `positive_E_result_equals_normal_path` | `bundle_sha256` igual; `ready`; 2 chunks | aqui | Router substituído por resposta sintética |
| E: 0 aberturas em checkout/venv, 0 subprocessos, 0 distribuições | `E_no_*`, `E_distribution_metadata_visible` | vazios | aqui | auditoria do próprio bootstrap (vale sob **P**) |
| Checkout, `.pyc`, venv e plugin afetam o caminho normal e não E | `checkout_source_tampered`, `pyc_planted_source_matches_commit`, `venv_installed_file_tampered`, `pydantic_plugin_distribution_planted` | normal executa; E inalterado | aqui | — |
| S_D vinculado ao lock de S_G; sem fallback | `D_not_bound_to_S_lock_refused`, `D_absent_no_fallback` | recusa; `No module named`, 0 aberturas na venv | aqui | — |
| Censo por origem | `census` | 35 S, 66 D, 2 nativos em memfd, 103/27/41 stdlib/ext/builtin; só no normal: `site`, `_sitebuiltins`, `_distutils_hack`; só em E: `pwd` | aqui | um percurso |
| Dados do target: 2 aberturas por arquivo, por path | `census.E_opens_outside_usr_proc_dev` + rastreio de pilha (sessão) | validate → relê | aqui | U4 |
| Números de recursos | `resources` | ver CONTRACT §8 | aqui (substitui RES do spike em 3.12) | este corpus/ambiente |
| Expansão por ocorrência e blob grande recusados antes | `exp_resources.json` | `budget_payload_bytes` (heap 17,7 / 0,06 MiB), `budget_nodes`, `budget_depth` | aqui | — |
| Cap por árvore é o de C3 | `single_tree_entry_cap_is_C3s` | `tree_unrepresentable` | aqui | — |
| Falhas sem S parcial; FDs/processos lineares; ausência ≠ sucesso | `write_failure_*`, `seal_failure_*`, `mid_capture_*`, `hung_consumer_*`, `consumer_exits_rc0_*`, `harness_fds_restored` | conforme esperado | aqui | falha de selo por injeção (monkeypatch) |
| `pydantic_core` sem RPATH; NEEDED só de sistema | `readelf -d` no wheel do lock (sessão, não persistido em JSON) | libgcc_s, librt, libpthread, libm, libc, ld-linux | aqui | cp311 manylinux2014 |
| Caller real e runner | forge: AgentEscala `develop@8537eb18` `agent-review-v2-analysis.yml`, `scripts/aiops/agent_review_v2_run.py` | `runs-on: [self-hosted, …, ct104, …]`; venv + `PYTHONPATH` | leitura de fonte | não executado |
| CAEM/SACR-AS sem workflow v2 | forge: listagem de `.github/workflows` | nenhum arquivo com review/agent | leitura | estado em 2026-09-26 |

## Herdado e não reexecutado

- Spike EXP-Q1 (0700/0444/`/proc/pid/fd`/`PR_SET_DUMPABLE` contra uma época C3 viva): citado apenas
  como motivação de "permissão/FD mantido ≠ imutabilidade" (PR352#5848970869).
- Censo do spike de que nenhum caller de produção de C3/G1 existe: não refeito como busca; o
  percurso de E aqui não usa C3/G1.
- Registros de #324/#353 (AR-C3-C4Q-KD-20260926-R1): usados como índice, não como reprodução.

## NOT_TESTED (e consequência)

- CT104: versão/ABI/ownership do interpretador, Yama, provisionamento root → U2/U3 continuam premissas.
- Runners hospedados com `setup-python` (toolcache do UID do runner) → piso 2 presumivelmente não
  atendido; não observado.
- Objetos em pack, alternates, repositório parcial → autenticação por argumento (buffer lido).
- memfd sob limite de memcg / OOM; kernels sem `F_SEAL_*` ou com outra semântica.
- Execução de programas por configuração do git durante `cat-file` (git é tratado como transporte
  não confiável; efeito possível seria disponibilidade ou código same-UID já presumido).
- Modo Router conectado; relançamento de brokers; `importlib.resources`; threads no bootstrap.
- Interpretador não oficial (Debian `/usr/bin/python3.11`), musl.
- Reprodução independente por outro operador/máquina.

## Defeitos do harness corrigidos durante a construção

Registrados para que a história não pareça linear. Nenhum mudou uma expectativa para acomodar um
resultado da engine ou do kernel.

1. Canal socket testado no número de FD errado (ENOENT) → número herdado real (ENXIO esperado mantido).
2. Marcador residual de um caso anterior creditado ao seguinte → limpeza antes de cada execução.
3. Payload de startup usava `open` antes de existir em `encodings/__init__` (o código **executou**,
   o marcador não foi gravado) → `posix.open`.
4. Caso de orçamento de nós media o cap por árvore de C3, não o orçamento por ocorrência → fixture
   com árvores pequenas; o cap de C3 virou caso próprio.
5. Bundle inacessível ao usuário 2000; fallback que criaria ref no checkout de origem substituído por
   repositório bare temporário.
6. C3 recusou overlayfs como workspace (`_require_workspace_name_semantics_v2`) → `/work` em tmpfs
   (domínio admitido de C3).

## Revisão

Registrada no corpo da Draft PR e nos seus comentários, com o head exato revisado; não repetida aqui.
