# #301-S1-B: experimentos P2 (evidência admitida no freeze)

```yaml
artifact_class: disposable_experiment
admitted_into: docs/engineering/agent-review-v2-301-s1b/ARCHITECTURE_FREEZE.md (§14, §17, §19, §30)
qualification_transfer: none        # nenhum resultado aqui qualifica implementação B1/B2/B3
production_authority: none          # nada aqui é código de produção nem API pública
production_app_changes: none
subject: master@d3f5946c4d0513def9f7c2018b63703a53df1cc7 (tree 9d134eb1e913d852748470c12235e0b6832fc4b8)   # realinhado na round 3b; rounds anteriores: ab92e89
toolchain_observed: {python: CPython 3.11.16, git: 2.43.0, kernel: 6.18.33.2-microsoft-standard-WSL2 x86_64}
```

`ExperimentalEvidence != Implementation != Qualification`.

Os resultados foram **estabelecidos durante o procedimento** registrado aqui. Não afirmam reprodutibilidade em qualquer checkout futuro. O harness pode ser reexecutado, e isso gera um resultado novo para aquele ambiente, sem reutilizar este.

## Arquivos

- `spike_reader.py`: leitor dedicado. Mecanismos **A** (`Popen` controlado) e **B** (fork/exec explícito); census tipado de fds; teardown por atribuição do kernel via pidfd; ablações. **Correction round 3:**
    - injeção `pre_exec_stall` em A e B;
    - no B, o canal de erro do handshake do exec é monitorado com `select` sob o deadline da unidade, que começa antes do `fork`: expirou → kill via pidfd + reap → `unit_deadline`;
    - `stdin_pipe` opcional no B;
    - ablação `handshake_deadline_off`.

    **Round 3b (B):**
    - seção crítica de sinais no `fork` e reset do maquinário de sinais do reader no filho;
    - bootstrap do filho terminal (`execve` ou `os._exit`);
    - o `select` do handshake inclui a ponta de leitura do wakeup;
    - após o deadline, só SIGKILL via pidfd, e o teardown limitado faz o resto;
    - helpers de SIGCHLD (B-LIF-11) e um registro de dono preenchido logo após o `fork`;
    - test double de SIGKILL que não completa (`d_state_sim_s`);
    - gancho `on_phase` no teardown;
    - ablações `no_fork_block`, `no_child_signal_reset`, `child_unwind`, `handshake_ignores_wakeup`, `blocking_reap_after_deadline`, `no_sigchld_normalize`, `sigchld_handler_only`;
    - iteração 2:
      - guarda do `fork()` (ablação `no_fork_restore_guard`);
      - reset de toda disposição `SIG_IGN` no filho (ablação `no_child_sigign_reset`);
      - teardown fecha o pidfd do dono uma vez e conta pidfds abertos;
      - orçamento do teardown = restante do envelope fixado antes do `fork` (`teardown_reserve`);
      - `raw_argv` para um alvo de exec não-Python
- `spike_child.py`: substituto do filho Git: observa NNP, `RLIMIT_AS`, cwd e fds como primeira ação após o exec; modos `report`, `stall`, `grandchild`, `hold`
- `harness.py`: matriz mecanismo × obrigação × positivo/contramodelo/ablação; detecta sobreviventes **de fora** do leitor, por token no argv; sai com código diferente de zero em qualquer falha; `gate_unavailable` nunca conta como PASS
- `git_facts.py`: P2b: snapshot **real** da S1-A (produtor de `master`, importado só para leitura), Git executado por descriptor (`fchdir` + `GIT_DIR=.`), gramática do header, paridade da preimage canônica sha1/sha256 e mutantes da preimage. **Round 3/3b:** a docstring de F3 foi estreitada (F3 mede só `<oid> missing` no stdout com rc 0; onde é respondido e se há descendentes não é observado). Na round 3b, o P2b foi reexecutado no tree realinhado (`b3657d3`, depois `d3f5946`) e o resultado é **idêntico** ao admitido
- `p2_spawn_spike_results.json`: resultado regenerado na round 3b com o `spike_reader.py` da 3b (44/44 PASS em 3 execuções; as 22 linhas B requalificam as obrigações do P2 para o spawn B da 3b)
- `p2b_git_facts_results.json`: resultado do P2b (PASS em sha1 e sha256)
- `p2c_corrections.py`: P2c, correction round 1: discriminadores de S1B-REV-01 (visão de fds em `preexec` vs. pós-exec), REV-02 (SIGTERM/SIGKILL, handler, PDEATHSIG, reader como init de pidns privado), REV-03 (`MSG_PEEK` em pipe; consumo real via `FIONREAD`) e REV-06 (`safe.directory` com dono estrangeiro simulado)
- `p2c_term_reader.py`: reader de término do P2c; reutiliza o caminho de spawn do mecanismo A de `spike_reader.py`. **O handler aqui LEVANTA `TerminationRequested`, o que não é o mecanismo congelado de B-LIF-09** (handler sem raise + `set_wakeup_fd`); o mecanismo final é qualificado em B2 (freeze §14)
- `p2c_corrections_results.json`: resultado do P2c, reexecutado na round 3b (14/14 PASS; estável em 3 execuções). REV01 reescrito: census estável sem filtro de faixa, mais o mutante `pass_fds=(extra,)`, que torna o witness RED
- `p2d_spawn_handshake.py`: P2d, correction rounds 3 e 3b. Cada linha declara proposição, tipo e observação. Cada família da 3b tem um **contrato de evidência**: `injection_confirmed`, controle positivo, controle negativo, ablação ou mutante, discriminador observado e limite do claim. Injeção não confirmada = FAIL. Cobre:
    - handshake do spawn, A vs B (S-*), incluindo R3B-H1 e R3B-H2 (test double);
    - canal de wakeup tipado (W-POS, CM-W1..W6, com as três barreiras isoladas em CM-W3a/b/d e removidas em CM-W3c);
    - sinal duplo em RUNNING;
    - máscara bloqueada (CM-R3-04..06);
    - sinais pendentes na entrada (R3B-S1..S3, inclusive como init de pidns);
    - sinal ao filho antes e depois do reset (R3B-S4, R3B-S4b);
    - sinal ao reader no HANDSHAKE (R3B-S5);
    - sinal e sinal duplo **dentro** do teardown, sincronizados num marcador (R3B-S6, R3B-S7);
    - SIGCHLD (R3B-C1..C3 e o mutante só-handler);
    - `BaseException` no bootstrap do filho (R3B-U1);
    - iteração 2:
      - `SIG_IGN` herdado (R3B-G1, alvo C `grep`);
      - `fork()` falha (R3B-F1);
      - sinal na seção crítica do fork (R3B-S8);
      - outcome derivado do teardown (R3B-O1);
      - envelope da unidade (R3B-H2);
      - pidfd do dono fechado (S-B1, W-POS)
- `p2d_spawn_handshake_results.json`: resultado do P2d, round 3b: `{rows: 64, contracts: 18}` (iteração 2), todos PASS, com injeção confirmada; estável em 3 execuções
- `p2d_state_machine_check.py`: round 3b: discriminador **estático** da máquina de estados única da §20 do freeze. Lê as arestas do próprio freeze e prova as leis L1–L10 (só `TEARDOWN` alcança `OUTCOME`; sem beco sem saída; saídas exigidas presentes; o handshake observa sinal e deadline; o bootstrap do filho é terminal; estados declarados; lei em prosa sem atalho). Os mutantes M1–M9 têm de tornar alguma lei RED
- `p2d_state_machine_results.json`: resultado do verificador no freeze deste head: leis GREEN e M1–M9 mortos

## Reprodução

```bash
python3.11 -I -S harness.py "$(command -v python3.11)" out.json
python3.11 git_facts.py <worktree-no-subject> out-git.json
python3.11 -I -S p2c_corrections.py "$(command -v python3.11)" out-p2c.json   # REV-02 T5–T7 exigem userns sem privilégio
python3.11 -I -S p2d_spawn_handshake.py harness "$(command -v python3.11)" out-p2d.json   # R3B-S1-pidns exige userns sem privilégio
python3.11 -I -S p2d_state_machine_check.py ../ARCHITECTURE_FREEZE.md out-sm.json
```

## Histórico das execuções (defeitos de harness encontrados e corrigidos; nenhum no mecanismo)

1. **Execução 1: 34 falhas.** O census tipado recusou o próprio positivo porque `subprocess.DEVNULL` abre `/dev/null` com **O_RDWR**. A regra estava certa (stdin do reader precisa ser somente leitura); a fixture estava errada. A fixture passou a abrir `/dev/null` `O_RDONLY`.
   - Isto também é um achado para o freeze (§8): `DEVNULL` não é somente leitura.
2. **Execução 2: 2 falhas (A-TF5-B e A-ABL-handle-B).** A injeção nunca aconteceu. O CPython 3.11 faz o bind `from _posixsubprocess import fork_exec as _fork_exec` no import, então o patch no módulo C não alcançava o ponto de chamada. As duas linhas não discriminavam porque não injetavam a própria falha (`Killed(M) BY IntendedDiscriminator`).
   - Correção: o ponto de injeção passou a ser `subprocess._fork_exec`.
3. **Execuções 3, 4 e 5: 44/44 PASS cada.** O arquivo JSON admitido é o da execução 5.
4. **Correction round 1, P2c: 13/13 PASS em 3 execuções.** Reproduz S1B-REV-01, 02, 03 e 06 antes de qualquer correção do freeze (`ReviewerObservation != FindingEstablished`).
   - T5–T7 rodam num userns sem privilégio, fora do domínio V1 (userns inicial). A semântica do kernel observada (a morte do init do pidns mata o namespace) independe do userns.
   - O witness no domínio exato exige root (B2/S1-D).
5. **Correction round 3** (review post-Ready 5370887198 em `4343dba`).
   - **P2d: 26/26 PASS em 3 execuções.**
     - A (`Popen`) sob stall pré-exec de 3 s com deadline de 1 s só devolve o controle em 3,002 s.
     - B devolve em 1,001 s (`unit_deadline`, 0 sobreviventes). A ablação B sem deadline no handshake leva 3,002 s. *(Na round 3 este README citava 1,002 s e 3,0 s; os valores do JSON admitido eram 1,001 s e 3,002 s.)*
     - Todos os CM-W e CM-R3 discriminam contra a própria ablação.
   - **P2 regenerado com o B limitado por deadline: 44/44 PASS em 3 execuções.**
   - **P2c REV01 reescrito: 14/14 PASS em 3 execuções.**
   - Defeito de harness corrigido durante o round: a ablação CM-W3 (wakeup herdável) era bloqueada pelo census tipado. Foi separada em duas linhas, a recusa do census e a ablação `no_census` com herança observada.
6. **Correction round 3b** (round 3 NOT_CONVERGED em `a727c7e`; #301 5919204387; branch realinhada ao master `d3f5946`, via `b3657d3`).
   - **P2d: 55 linhas e 14 contratos, 0 falhas, em 3 execuções.**
   - **P2 (44/44) e P2c (14/14):** 3 execuções cada.
   - **P2b:** idêntico no tree realinhado.
   - **Verificador da §20:** GREEN, M1–M9 mortos.
   - **Primeira execução da 3b: 4 falhas, todas de expectativa, nenhuma de mecanismo:**
     - CM-W3c: o reset do filho passou a fechar as pontas de wakeup, uma **terceira** barreira independente. As linhas de isolamento agora desligam as outras duas explicitamente (CM-W3a/b/d) e CM-W3c remove as três.
     - CM-R3-04..06, ablações: o passo 1 do bootstrap novo bloqueia todo o CONTROLLED, então a ablação sem o passo 7 deixa `[1,2,15]` bloqueado em vez de `[s]`. O discriminador (pendente, sem wakeup, cerca de 2,9 s) não mudou; a expectativa passou a ser `s ∈ bloqueados`.
   - Sonda de semântica (fora do harness):
     - `signal.signal` do CPython instala sem `SA_NOCLDWAIT`;
     - `SIG_DFL` com `SA_NOCLDWAIT` auto-reapeia;
     - `SIG_IGN` sobrevive ao execve;
     - o execve zera `sa_flags`.
7. **Round 3b, iteração 2** (revisão independente e Codex 5371956614 em `8cad5d3`).
   - **P2d: 64 linhas e 18 contratos, 0 falhas, em 3 execuções.**
   - **P2 (44/44) e P2c (14/14):** 3 execuções cada.
   - **Verificador da §20:** L1–L10 GREEN, M1–M9 mortos.
   - **Defeito de witness corrigido:** a primeira versão de R3B-G1 lia `SigIgn` do substituto CPython (`spike_child.py`). O CPython reignora SIGPIPE/SIGXFSZ na própria inicialização, então positivo e ablação davam o mesmo 0x1001000. O witness passou a exec'ar `grep SigIgn /proc/self/status`, um programa C.
