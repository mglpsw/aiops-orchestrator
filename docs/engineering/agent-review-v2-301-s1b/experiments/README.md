# #301-S1-B: experimentos P2 (evidência admitida no freeze)

```yaml
artifact_class: disposable_experiment
admitted_into: docs/engineering/agent-review-v2-301-s1b/ARCHITECTURE_FREEZE.md (§14, §17, §19, §30)
qualification_transfer: none        # nenhum resultado aqui qualifica implementação B1/B2/B3
production_authority: none          # nada aqui é código de produção nem API pública
production_app_changes: none
subject: master@ab92e89f096b391bc50759afa3d0f6050881633a (tree a811df6344ab5ba450b3a1b5fd12cd45d0477552)
toolchain_observed: {python: CPython 3.11.16, git: 2.43.0, kernel: 6.18.33.2-microsoft-standard-WSL2 x86_64}
```

`ExperimentalEvidence != Implementation != Qualification`.

Os resultados foram **estabelecidos durante o procedimento** registrado aqui. Não afirmam reprodutibilidade em qualquer checkout futuro. O harness pode ser reexecutado, e isso gera um resultado novo para aquele ambiente, sem reutilizar este.

## Arquivos

| Arquivo | Papel |
|---|---|
| `spike_reader.py` | leitor dedicado. Mecanismos **A** (`Popen` controlado) e **B** (fork/exec explícito); census tipado de fds; teardown por atribuição do kernel via pidfd; ablações |
| `spike_child.py` | substituto do filho Git: observa NNP, `RLIMIT_AS`, cwd e fds como primeira ação após o exec; modos `report`, `stall`, `grandchild`, `hold` |
| `harness.py` | matriz mecanismo × obrigação × positivo/contramodelo/ablação; detecta sobreviventes **de fora** do leitor, por token no argv; sai com código diferente de zero em qualquer falha; `gate_unavailable` nunca conta como PASS |
| `git_facts.py` | P2b: snapshot **real** da S1-A (produtor de `master`, importado só para leitura), Git executado por descriptor (`fchdir` + `GIT_DIR=.`), gramática do header, paridade da preimage canônica sha1/sha256 e mutantes da preimage |
| `p2_spawn_spike_results.json` | resultado da execução 5 (44/44 PASS) |
| `p2b_git_facts_results.json` | resultado do P2b (PASS em sha1 e sha256) |
| `p2c_corrections.py` | P2c, correction round 1: discriminadores de S1B-REV-01 (visão de fds em `preexec` vs. pós-exec), REV-02 (SIGTERM/SIGKILL, handler, PDEATHSIG, reader como init de pidns privado), REV-03 (`MSG_PEEK` em pipe; consumo real via `FIONREAD`) e REV-06 (`safe.directory` com dono estrangeiro simulado) |
| `p2c_term_reader.py` | reader de término do P2c; reutiliza `spike_reader.py` sem alterá-lo. **O handler aqui LEVANTA `TerminationRequested`, o que não é o mecanismo congelado de B-LIF-09** (handler sem raise + `set_wakeup_fd`); o mecanismo final é qualificado em B2 (freeze §14) |
| `p2c_corrections_results.json` | resultado do P2c (13/13 PASS; estável em 3 execuções) |

## Reprodução

```bash
python3.11 -I -S harness.py "$(command -v python3.11)" out.json
python3.11 git_facts.py <worktree-no-subject> out-git.json
python3.11 -I -S p2c_corrections.py "$(command -v python3.11)" out-p2c.json   # REV-02 T5–T7 exigem userns sem privilégio
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
