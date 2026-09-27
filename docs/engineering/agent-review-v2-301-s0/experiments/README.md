# #301 S0 — experimentos opt-in

**Experimentais.** Nada aqui é código de produção, nada em `app/`, `scripts/` ou nos workflows
importa estes arquivos, e o pytest não os descobre (`pytest.ini: testpaths = tests`; nenhum arquivo
se chama `test_*`). Servem de evidência para [`../CONTRACT.md`](../CONTRACT.md); o índice por
afirmação está em [`../EVIDENCE.md`](../EVIDENCE.md).

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
3. executa cada `exp_*.py` como o usuário 2000 com `python3.11 -I -S`, ambiente vazio.

Cada script imprime JSON com `expected`, `observed` e `pass` por caso; `pass` é calculado, não
declarado. Casos sem `expected` (em `exp_process_channel`) são observações.

## Arquivos

| Arquivo | Papel |
|---|---|
| `s0_bootstrap.py` | dono único do formato do container, do compromisso de selo e da validação do consumidor; também é o bootstrap do filho (`-c`) |
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
| `exp_functional.py` | engine real executada só de `S_G`/`S_D`; paridade; contramodelos; censo |
| `exp_resources.py` | expansão, orçamentos, falhas e ownership |
| `results/py311-20260926/` | saída da execução registrada + `SCRIPTS.sha256` dos arquivos que a produziram |

## Limites

Ambiente único (WSL2 6.18, container Debian 12, CPython 3.11.16, git 2.39.5, Yama=1). Nenhum
resultado aqui qualifica CT104, runners hospedados, outro kernel ou outra build do interpretador.
Um timeout protege o harness; não prova limite de complexidade.
