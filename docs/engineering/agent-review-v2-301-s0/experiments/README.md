# #301 S0 — experimentos opt-in

**Experimentais.** Nada aqui é código de produção, nada em `app/`, `scripts/` ou nos workflows
importa estes arquivos, e o pytest não os descobre (`pytest.ini: testpaths = tests`; nenhum arquivo
se chama `test_*`). Servem de evidência para [`../CONTRACT.md`](../CONTRACT.md); o índice por
afirmação está em [`../EVIDENCE.md`](../EVIDENCE.md).

```yaml
current_candidate: C_PRIVILEGE_SEPARATED_IMMUTABLE_SNAPSHOT
rejected_predecessors: [A_LIVE_GIT_TRANSPORT, B_MUTABLE_PRIVATE_SNAPSHOT]
production_implementation: none
```

## Reproduzir

Requer Docker e rede (imagem + wheels do lock). Não instala nada no host; só escreve em `<results>`.

```bash
bash docs/engineering/agent-review-v2-301-s0/experiments/run_py311.sh . 9abcde6420a59b814b5faaff10ca5904c5d23370 /tmp/s0-results
```

O runner:

1. cria um bundle contendo só o commit pedido (repositório bare temporário; nenhum ref é criado
   no checkout de origem);
2. em `python:3.11-bookworm@sha256:b99029c9…` com `/work` em **tmpfs** (C3 recusa overlayfs como
   workspace de materialização; só duas fixtures precisam de M), prepara:
   - `/opt/toolrepo-tcb` — cópia **root-owned** do toolrepo (código do produtor/launcher e a
     regra de árvore de C3 que o produtor importa);
   - `/work/toolrepo`, `/work/venv` — checkout e venv **do usuário 2000**, criados pelo script do
     próprio repositório (`install-agent-review-toolrepo.sh --toolrepo-sha`);
3. executa cada `exp_*.py` como o usuário 2000 com `python3.11 -I -S -B`, ambiente vazio; o produtor
   importa a regra de árvore do C3 só de `/opt/toolrepo-tcb`;
4. arquitetura C: como root (uid 0 do container = produtor com privilégio separado), cria
   `/srv/<nonce>-func/{committed (0755), staging (0700)}`, publica o snapshot do toolrepo com
   `s0_snapshot_c.py` (`producer_functional.json`) e passa o caminho publicado a
   `exp_functional.py`, cujo leitor roda como o usuário 2000; depois roda `exp_arch_c.py` como root,
   que chama o produtor como uid 0 e o leitor **sempre** como uid 2000 (`setpriv`).

Cada script imprime JSON com `expected`, `observed` e `pass` por caso; `pass` é calculado, não
declarado. Casos sem `expected` (em `exp_process_channel` e `non_utf8_name_S_vs_C3`) são observações.

## Arquivos

| Arquivo | Papel |
|---|---|
| `s0_bootstrap.py` | dono único do formato do container, do compromisso de selo e da validação do consumidor; também é o bootstrap do filho (`-c`) |
| `s0_snapshot_c.py` | **arquitetura C (candidata)**: produtor físico com privilégio separado — sonda de alternate limitada pelo mesmo orçamento de entradas (a da G1C de produção não é usada nem alterada), escrita repetida até completar, cópia por descritor sem Git/inflate/metadata da fonte, alternates autorizados achatados, orçamento físico, staging → finalize → `rename` (commit point), recibo |
| `s0_reader_c.py` | **arquitetura C (candidata)**: leitor que roda como o runner — estabelece o `ReaderPrincipal` (uid/gid real = efetivo = salvo = filesystem = runner esperado; `CapEff/CapPrm/CapInh/CapAmb` = 0; escrita checada com credenciais efetivas), exige caminho absoluto canônico sem symlink, recusa snapshot mutável por ele (componentes de `/` para baixo e nós), cobra cada ocorrência e os paths durante a caminhada, passa ao parser de tree só o restante do orçamento de nós, recusa sobrevivente do teardown em qualquer desfecho, Git local contido (prazo, `RLIMIT_AS`, subreaper + teardown), raiz derivada de `C`, mapa endereçado por conteúdo, selo + re-derivação |
| `s0_snapshot.py` | **arquitetura B (REJEITADA; histórico)**: snapshot físico privado sem remoto (aquisição por descritor da G1C, esqueleto com formato de objeto, sem `verify-pack`), identidade do snapshot |
| `s0_capture.py` | leitor de objetos com hash-on-read, construção de `S_G`, orçamentos por ocorrência |
| `s0_deps.py` | `S_D`: lock (dentro de `S_G`) → wheel → membros verificados pelo RECORD |
| `s0_launch.py` | launcher: interpretador absoluto, `env={}`, `-I -S`, `pass_fds`, socketpair |
| `s0_driver_engine.py` | percurso real da engine (espelha o caller v2 do AgentEscala) |
| `s0_fixture.py` | fixtures Git com identidade própria (sem config global) |
| `exp_n1_auth.py` | autenticação no consumo: commit/tree/blob, sha1/sha256, ablação, testemunha herdada |
| `exp_structure.py` | fidelidade A/A2, paridade com árvore declarada e com C3, recusas |
| `exp_capture_stability.py` | selos, janela pré-selo, M mutado, substituição de binding |
| `exp_process_channel.py` | integridade de processo e canal de resultado |
| `exp_bootstrap_env.py` | configuração anterior ao 1º import; `-I` vs `-S`; piso root-owned |
| `exp_functional.py` | engine real executada só de `S_G`/`S_D`; paridade; contramodelos; censo; com o 9º argumento, a captura de `S_G` passa pelo leitor de C sobre o snapshot publicado |
| `exp_resources.py` | expansão, orçamentos, falhas e ownership |
| `exp_arch_c.py` | **arquitetura C**: C1–C11 (+ contra-controles do caminho do leitor e dos orçamentos adicionados após a revisão de `a858dc9`; K1–K4 e RC-5 com ablação no corte terminal) (mutação negada pelo kernel, metadata da fonte, injeções, objeto ausente, bomba, raiz derivada, neto `setsid`, clone parcial, alternates, crash antes do commit point, leitor como o runner), positivos, vetor de recursos em dois domínios, censo |
| `exp_snapshot.py` | arquitetura B (**REJEITADA**; reexecutado como histórico): Spike B portado + prazo, forja de pack/`.idx`, sha256, identidade imutável, orçamentos, censo de órfãos |
| `exp_deps.py` | recusas de S_D em wheels sintéticos (identidade, tags, RECORD, zip bomb, colisões, ELF) |
| `results/py311/` | saída da execução registrada + `SCRIPTS.sha256` dos arquivos que a produziram |
| `sd_future/` | **preservado para a futura slice S_D, não é evidência de S0**: spike do leitor único com cobrança e reprodução de R2-1/R2-8 sobre `s0_deps.py` congelado (execução no host 3.12; `python3 -I -S -B <arquivo> <experiments> <scratch>` para `repro_r2.py` — `<scratch>` precisa existir —, `<scratch>` para o spike) |

## Limites

Ambiente único (WSL2 6.18, container Debian 12, CPython 3.11.16, git 2.39.5, Yama=1). A separação
de privilégio é o uid 0 de um container efêmero (`SpikeContainerRoot != ProductionHostAuthority`):
nada aqui qualifica o produtor de produção (U3) nem a política do host (#331). Nenhum
resultado aqui qualifica CT104, runners hospedados, outro kernel ou outra build do interpretador.
Um timeout protege o harness; não prova limite de complexidade.
