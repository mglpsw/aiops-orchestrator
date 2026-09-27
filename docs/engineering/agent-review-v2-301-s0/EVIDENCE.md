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
results: experiments/results/py311/
scripts: experiments/results/py311/SCRIPTS.sha256   # os arquivos que produziram estes resultados
outcome: 123/123 casos com expectativa passaram (8 scripts); 2 observações sem expectativa em
         exp_process_channel e 1 em exp_structure (non_utf8_name_S_vs_C3); único stderr = aviso
         esperado do zipfile no fixture de membro duplicado (exp_deps.stderr)
reproduction: própria (esta sessão); não é reprodução independente
```

Os valores esperados estão escritos em cada script antes da execução registrada. Execuções de
ensaio em CPython 3.12.3 no host e as execuções anteriores em 3.11 **não** são evidência registrada
deste head; a do head `3d426e1` continua no histórico git.

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
| Substituição de binding | `binding_*` (6) | recusas esperadas; driver não executou | aqui | — |
| Pipe forjável; socketpair não | `exp_process_channel.json` | `FORGED` / `ENXIO` | aqui | — |
| Não-ancestral mesmo UID vs consumidor | `same_uid_non_ancestor_vs_*` | sem attach/`/proc/pid/mem`; lista FDs se dumpable | aqui | Yama=1; produtor/launcher não observados |
| Startup: `pyvenv.cfg`, `LD_PRELOAD`, `-I` vs `-S` | `exp_bootstrap_env.json` | conforme esperado (14 casos) | EXP-F1 **reexecutada** + novos | — |
| Piso root-owned | `floor` (23 caminhos), `floor_all_root_owned_and_not_writable` | `true` | aqui | **só neste container** |
| S_D: 25 recusas e controles | `exp_deps.json` | todas as razões esperadas; controle puro e nativo sem caminho aceitos | aqui (novo nesta rodada) | wheels sintéticos; `cc` do container |
| Zip bomb recusado antes de inflar | `compressed_member_refused_before_inflate` | 200 MiB recusado, heap 0,27 MiB | aqui | — |
| Engine real só de S_G/S_D com paridade | `exp_functional.json` `positive_E_result_equals_normal_path` | `bundle_sha256` igual; `ready`; 2 chunks | aqui | Router → resposta sintética |
| E: 0 aberturas em checkout/venv, 0 subprocessos, 0 distribuições | `E_no_*`, `E_distribution_metadata_visible` | vazios | aqui | audit do bootstrap: só nível Python; vale sob **P** |
| Checkout, `.pyc`, venv, plugin afetam só o caminho normal | `checkout_source_tampered`, `pyc_planted_*`, `venv_installed_*`, `pydantic_plugin_*` | normal executa; E inalterado | aqui | — |
| D vinculado ao lock; sem fallback | `D_not_bound_*`, `D_absent_no_fallback` | recusa; `No module named` | aqui | vínculo de rótulo |
| D não estende pacotes de S/stdlib nem sombreia stdlib | `D_cannot_extend_S_or_stdlib_packages` | `ModuleNotFoundError` ×3, `LookupError`, `json` da stdlib | aqui; **defeito reproduzido pela revisão no head `3d426e1`** | — |
| Produtor importa C3 só da cópia root-owned | `run_py311.sh` (arg `/opt/toolrepo-tcb`), `exp_functional.py` | — | construção | observado por configuração, não por auditoria |
| Censo por origem; `pwd` atribuído | `census` | 35 S, 66 D, 2 nativos; `pwd` via `zoneinfo`→`sysconfig`→`expanduser` | aqui | um percurso |
| Recursos de S/D/E | `resources` | CONTRACT §8 | aqui | este corpus/ambiente |
| Commit/tree/blob/expansão/profundidade limitados antes | `exp_resources.json` | tree 16,7 MB, commit 16 MiB, blob 32 MiB recusados com heap 0,06 MiB; expansão 125 MiB recusada em 17,8 MiB; profundidade 110 | aqui | — |
| Falhas sem S parcial; ownership linear | `write_failure_*`, `seal_failure_*`, `mid_capture_*`, `launcher_pre_spawn_*`, `hung_*`, `consumer_exits_rc0_*` | conforme esperado | aqui | falha de selo por injeção |
| Caller real, runner, targets | forge: AgentEscala `develop@8537eb18`; listagem de workflows de caem/sacr-as | CT104 self-hosted; sem v2 em CAEM/SACR-AS | leitura de fonte | não executado |

## Herdado e não reexecutado

- Spike EXP-Q1 (permissões/FD mantido contra uma época C3 viva): citado apenas como motivação
  (PR352#5848970869).
- Censo do spike de que não existe caller de produção de C3/G1: não refeito como busca.
- Registros de #324/#353 (AR-C3-C4Q-KD-20260926-R1): usados como índice.

## NOT_TESTED (e consequência)

- CT104: versão/ABI/ownership do interpretador, Yama, provisionamento root → U2/U3 continuam premissas.
- Runners hospedados com `setup-python` → piso 2 presumivelmente não atendido; não observado.
- Objetos em pack, alternates, repositório parcial → autenticação por argumento.
- memfd sob limite de memcg/OOM; outros kernels.
- Sinais de mesmo UID contra produtor/launcher/filho; integridade de processo do produtor e do launcher.
- Execução de programas por configuração do git durante `cat-file`.
- Modo Router conectado; relançamento de brokers; threads no bootstrap.
- Interpretador não oficial (Debian `/usr/bin/python3.11`), musl.
- Reprodução independente por outro operador/máquina.

## Rodada de revisão 1 (head `3d426e1`) → correções neste head

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
