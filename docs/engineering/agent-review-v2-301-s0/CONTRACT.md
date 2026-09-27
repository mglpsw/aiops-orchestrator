# #301 S0 — contrato proposto do subject de execução autenticado (S) e da fronteira de confiança

```yaml
status: PROPOSED_NOT_RATIFIED
slice: S0_contract_and_experimental_evidence   # nome de trabalho; não é claim normativa, milestone nem revisão CAEM
work_owner: "#301"          # roadmap: #46; núcleo: #80
base: 9abcde6420a59b814b5faaff10ca5904c5d23370   # tree 93143d70ed410771776f5f2cdb48d7f8e3f5ed9b
production_S_implemented: false
production_E_implemented: false
evidence_index: EVIDENCE.md
experiments: experiments/            # opt-in; fora de app/, scripts/ e da descoberta do pytest
```

### Decisão de corte do mantenedor (2026-09-27, após STOP/REDESIGN da rodada 2)

```yaml
S0_decision:
  S_G:  {contract: keep_and_finish, status_target: ready_for_adjudication}
  S_D:
    architecture: preserve_as_proposed
    proof_claims: {resource_boundedness: future_S_D_slice, native_closure: future_S_D_slice,
                   loader_completeness: future_S_D_slice}
    mandatory_countermodels: [R2-1, R2-2, R2-3, R2-7, R2-8]   # PR #355, comentário 5852251027
  E: {remains_future: true}
```

S0 fecha o contrato de **S_G** como componente. S_D e E continuam **propostos**: a evidência
deles aqui é de viabilidade, não de qualificação. Nenhuma obrigação foi apagada; as de S_D têm
owner futuro e contramodelos obrigatórios (§7.3).

Este documento é a **única** fonte legível da proposta. `EVIDENCE.md` aponta para cá e não
repete norma. Vocabulário epistemológico e critérios de STOP/REDESIGN pertencem a
[`STRUCTURAL_CHANGE_PREFLIGHT.md`](../STRUCTURAL_CHANGE_PREFLIGHT.md); nada aqui os substitui.
ADRs CAEM 0011/0012/0016 (lidas em `mglpsw/caem@854e321a`) são **referência de desenho**, sem
autoridade (`config/caem/caem-3.0-f0.pin.json`: `authority_effect: none`).

## 1. Pergunta, decisões preservadas e distinções

> Qual representação S, sob quais autoridades e premissas de ambiente, permite que o futuro
> consumidor E execute o AgentReview real sem retornar a bytes mutáveis não autenticados?

`G` = subject Git admitido; `M` = materialização mutável de C3; `Q` = igualdade observacional
`G == M` sob quiescência, no domínio ratificado em #333 (Linux + C3 admitido + `PC_NAME_MAX`
positivo pelo descritor). Q **não** é transferido a S: Q não exclui escritores e não prova os bytes
carregados. O objetivo de #301 (obter S e E) está decidido; esta slice propõe a **forma** de S, o
envelope de execução e a base de confiança.

Três subjects, que o desenho mantém separados:

| Subject | O que é hoje no caller real | Papel em S/E |
|---|---|---|
| **Engine** (toolrepo) | checkout de `aiops-orchestrator` num SHA completo, importado via `PYTHONPATH` | **código executado**: entra em S |
| **Target / PR** | diff reacquirido pela API; profile/policy/artefatos do checkout *base* do target | **dado**: nunca é código autorizado a executar; não entra em S |
| **Dependências + runtime** | venv criada por `install-agent-review-toolrepo.sh` (`pip --require-hashes`) sobre o `python3` do host | wheels do lock → S_D; interpretador/stdlib → TCB |

Há um quarto papel que o caller real mistura com a engine: o **adapter do target**
(AgentEscala `scripts/aiops/agent_review_v2_run.py`, base-owned) é `__main__` do mesmo processo,
importa a engine e chama o Router entre `build` e `bind`. AgentEscala, CAEM e SACR-AS são
consumidores heterogêneos; nenhum vira ramo especial da engine. Ver §9 (U1).

## 2. Claim de S0 (≤ 3 frases), propostas restantes e non-claims

> **S_G-CLAIM.** Dado um commit esperado `C` fornecido por autoridade externa (#319) e um produtor
> íntegro, um S_G admitido é um objeto selado cujos bytes foram todos autenticados **no instante
> da leitura** contra `C`, pela cadeia commit → tree → blob no formato de objeto de `C`, e que
> codifica sem perda as distinções da contract A/A2 de C3 sobre a árvore de `C`, com todo corpo de
> objeto cobrado antes de ser lido. Depois do instante de compromisso (selos
> `F_SEAL_{WRITE,GROW,SHRINK,SEAL}` lidos de volta e conteúdo selado re-hasheado igual ao digest dos
> bytes autenticados), nenhum processo sem `CAP_SYS_ADMIN`/root, inclusive do mesmo UID, altera
> bytes, índice ou tamanho de S_G. Um consumidor recebe S_G por descritor herdado e a identidade
> esperada `(algoritmo, C)` e o digest por canal separado controlado pelo launcher, e valida selos,
> digest e identidade sobre o mesmo buffer que consome.

**Propostas que S0 não qualifica** (arquitetura preservada, prova futura):
- **S_D**: dependências como segundo container selado vinculado ao lock em S_G (§4–§5, §7.3).
- **E**: launcher/bootstrap/loader/canal (§9, §7.2). A execução da engine real só a partir de S_G +
  S_D, com resultado idêntico ao caminho normal, é evidência de **viabilidade**, não claim de S0.

Premissas (não impostas por S): kernel Linux com memfd seals (observado só em 6.18/WSL2); root e
kernel confiáveis; `C` correto (#319); launcher/produtor/bootstrap vindos de local não gravável pelo
UID do runner (§6, U3); a mesma premissa de integridade de processo de §3 vale para **produtor e
launcher** (o heap do produtor guarda bytes autenticados antes do selo; o launcher guarda os digests
esperados).

Non-claims: integridade do **processo** além da premissa de §3; disponibilidade (um ator same-UID
pode matar/esgotar recursos); integridade dos **dados do target** (§9 U4); autenticação do
interpretador/stdlib (piso por ownership, §6); proveniência do anchor (#319); correção do
comportamento da engine; portabilidade não-Linux; qualificação de CT104; modo Router conectado;
relançamento de brokers/supervisor; confidencialidade de S (não é segredo).

## 3. Atores e capacidades (domínio de ameaça proposto)

"Resiste a same-UID" é decomposto por operação. **O** = integridade do objeto selado; **P** =
integridade do processo consumidor; **R** = canal de resultado; **A** = disponibilidade.

| Ator | Capacidades relevantes | Coberto por S/E | Não coberto |
|---|---|---|---|
| Conteúdo Git/PR hostil (target) | dados arbitrários no diff/artefatos | nunca executado; parse pelas autoridades de ingress existentes | semântica dos dados (fora de S) |
| Conteúdo do commit da engine | árvore arbitrária sob `C` admitido | recusas estruturais (C3) e orçamentos por ocorrência (EXP-RES) | `C` errado → #319 |
| Writer de filesystem mesmo UID (não-ancestral) | reescrever/renomear checkout, `.git/objects`, venv, `__pycache__`, `pyvenv.cfg`, `.pth`, wheels, dados do target; abrir `/proc/<pid>/fd/N` de processos *dumpable*; escrever em pipe reaberto; tocar memfd **antes** do selo; adicionar selo estranho; manter mapeamento | **O**: pós-compromisso nenhuma escrita/grow/shrink/punch/mmap-write/mprotect (EXP-CAPTURE); pré-compromisso detectado ou recusado; adulteração de objeto Git recusada por hash (EXP-N1). **R**: socketpair não reabrível (EXP-PROC). Bytes carregados independem de M/venv/pyc/plugins (EXP-FUNC) | **A** (kill, OOM, disco); leitura de S (não secreto); dados do target lidos por path |
| Worker legítimo concorrente | mesmo que acima, benigno | S não compartilha estado mutável | — |
| Ator que altera configuração do processo (não o arquivo) | env (`LD_PRELOAD`, `PYTHON*`, `HOME`), cwd, `pyvenv.cfg`, user site, `.pth`, `sitecustomize` | launcher: interpretador root-owned por caminho absoluto, `env={}`, `-I -S`, `cwd=/` (EXP-BOOT) | se o ator **for** o launcher ou um ancestral |
| Interferência no processo (ptrace, `/proc/pid/mem`) | com Yama `ptrace_scope ≥ 1`, não-ancestral não faz attach nem escreve memória; lista FDs enquanto dumpable | **P** somente sob a premissa Yama≥1 ∧ ator não-ancestral, para consumidor, produtor e launcher (EXP-PROC observou o consumidor) | ancestrais (runner/agent) são confiados; `ptrace_scope=0` quebra **P** |
| Sinais (`kill`, `SIGINT`, `SIGSTOP`) de mesmo UID | interromper ou matar produtor, launcher ou filho | nenhum resultado parcial vira S/sucesso (301S-LIFE) | **A**; não testado com sinais reais |
| `git` filho do produtor | config do repositório (mesmo UID) pode fazer o git executar programas (ex.: busca de promisor) | bytes servidos são verificados por hash; o filho não é ancestral do produtor | **A**; código same-UID já está no domínio; não testado |
| Bootstrap, consumidor, produtor, futuro produtor de receipt | papéis TCB | ver §6 | auto-autenticação recursiva |
| root / kernel | tudo | confiados (declarado) | — |
| UID diferente | DAC | fora | — |

Redução proposta em relação ao texto histórico de #301 ("exclusively satisfied from the verified
subject"): **P** vale só sob a premissa Yama/não-ancestral e **A** não é garantida. Esta redução é
**proposta para adjudicação**, não ratificada aqui.

## 4. Representação S proposta

**Forma.** Dois containers com o mesmo formato canônico (protótipo:
[`experiments/s0_bootstrap.py`](experiments/s0_bootstrap.py) `serialize`/`parse`), cada um num
único memfd selado:

- `S_G` — **árvore completa** de `C` (não um subconjunto escolhido): cabeçalho
  `(algoritmo, commit, root_tree)`, depois nós em ordem canônica de bytes brutos do path
  (`memcmp`), cada nó `(kind ∈ {tree, regular, executable, symlink}, path bruto, oid, len, payload)`.
  Diretórios explícitos e vazios são nós; symlink é `kind=symlink` com o target como bytes.
- `S_D` — conjunto de dependências: cabeçalho `(wheelset-sha256, sha256(lock), sha256(manifest de
  wheels))`, nós = membros de wheels (inclusive `.so`) com o sha256 de cada membro.

Índice, offsets e tamanhos estão **dentro** dos bytes selados e cobertos pelo mesmo digest; não há
índice externo substituível. O parser recusa truncamento, bytes finais, índice fora de ordem ou
duplicado.

**Identidades distintas** (nunca fundidas): `container_digest` = sha256 dos bytes de `S_G` (ou
`S_D`); `subject_identity` = `(algoritmo, C)`; `dependency_identity` = `sha256(lock)` + digests
dos wheels; `invocation` = interpretador/flags/env/argv do launcher; `observation` = o que o
bootstrap registrou. Hoje o repositório tem campos de identidade de execução (G1); **nenhum campo
de wire/schema novo é proposto aqui**.

**Instante de compromisso.** Antes dele a captura pode falhar livremente; nenhum descritor sai do
produtor. Compromisso = `F_ADD_SEALS(WRITE|GROW|SHRINK|SEAL)` ok ∧ `F_GET_SEALS ⊇` esse conjunto ∧
`sha256(pread(conteúdo selado)) == sha256(bytes autenticados)`. Falha em qualquer passo fecha o fd
uma vez e não retorna nada. A re-verificação pós-selo é o discriminador contra o escritor da janela
pré-selo (EXP-CAPTURE: ablação sem re-hash **compromete conteúdo adulterado**).

**Não** são substitutos de estabilidade: permissões `0700`/`0444`, mutex Python, path resolvido,
FD mantido aberto, ou repetir hashes sobre M (spike EXP-Q1; K03/K05 de #324).

## 5. Cadeia de autenticação

```text
C (esperado, via #319)
 └─ commit object  ── hash(type ‖ ' ' ‖ len ‖ NUL ‖ body) == C ; 1ª linha "tree <oid>"
     └─ tree objects ── mesmo hash; bytes interpretados por C3 `_parse_tree_data` (dono da regra)
         └─ blobs      ── mesmo hash; ESTE buffer é o payload embutido em S_G
             └─ blob requirements-agent-review.lock  (dentro de S_G)
                 └─ sha256 por distribuição/versão
                     └─ wheel lido UMA vez; sha256 ∈ lock   (artifact adquirido)
                         └─ membros extraídos DESTE buffer; cada um == RECORD do wheel
                             └─ S_D ; header vincula sha256(lock)
```

- **Algoritmo** vem do comprimento do `C` esperado (40 → sha1, 64 → sha256) e é conferido com
  `git rev-parse --show-object-format`; divergência recusa (`object_format_mismatch`). Nada de
  SHA fixo por conveniência; a regra JSON de self-hash do produto não se aplica a objetos Git.
- `git cat-file --batch` é **transporte não confiável**: o spike e EXP-N1 mostram que ele serve,
  com rc 0, bytes que não hasheiam ao oid pedido. A autenticação é do buffer lido, logo independe
  do storage (loose, pack, alternates) — argumento, observado apenas para loose (§ EVIDENCE).
- **Bytes autenticados = bytes incorporados.** Hash de uma leitura e cópia de outra reabre a janela
  (EXP-N1 `MUTANT_verify_then_reread`). A única transformação é a extração de membros do wheel, que
  preserva a equivalência pelo RECORD autenticado junto com o wheel; nomes duplicados, `..`,
  absolutos, symlinks, `.data/`, `.pth` e membros fora do RECORD são recusados.
- A venv **não** é fonte de bytes: `pip --require-hashes` autentica o download que o pip fez, não os
  arquivos que um processo lê depois (EXP-FUNC: venv adulterada e plugin plantado executam no
  caminho normal).
- Uma cópia selada com digest próprio **não** autentica a origem: o digest de `S_G` só significa
  algo porque cada nó foi verificado contra `C`, e `C` vem de #319.
As regras abaixo para S_D são **propostas** (§7.3): o protótipo as observa no corpus sintético, mas
a ingestão de S_D não é limitada em `1e2453e` (R2-1/R2-2) e sua closure nativa está incompleta (R2-3).

- **O lock precisa ser nó `regular` de `S_G`**; o texto-alvo de um symlink nunca autoriza S_D
  (produtor e consumidor verificam).
- **Identidade do wheel vem de dentro dos bytes autenticados**, não do nome do arquivo: stem do
  `.dist-info`, `Name`/`Version` do `METADATA` e tags do `WHEEL` (expandidas como no nome, pois
  geradores como maturin gravam tags comprimidas) têm de coincidir com a entrada do lock; as tags
  têm de ser compatíveis com o interpretador alvo, cuja tag (`cp311`) fica no cabeçalho selado de S_D
  e é reconferida pelo consumidor.
- O lock é lido de forma **mais estrita que o pip**: nome duplicado, marker/extra e hash fora de
  token `--hash=` (inclusive em comentário) são recusados, nunca mesclados.
- Tamanho de cada membro é checado pelo `ZipInfo` **antes** de descomprimir; colisões
  arquivo/diretório (no mesmo wheel ou entre wheels) são recusadas.
- Nativos: a seção dinâmica ELF é inspecionada; `RPATH`/`RUNPATH` ou `DT_NEEDED` com `/` são
  recusados, de modo que a closure nativa fique no caminho do ld.so root-owned (TCB).

## 6. Closure, dependências e TCB

### 6.1 Censo do percurso escolhido

**Recorte.** O percurso material é o que o caller real v2 executa (AgentEscala
`agent-review-v2-analysis.yml` → `agent_review_v2_run.py` @ `develop 8537eb18`): profile YAML →
policy de agrupamento → `parse_unified_diff` → `assemble_manifest_from_diff_v2` →
`build_chunk_payloads_from_profile_v2` → `emit_payload_set_v2` → `bind_chunk_response_v2` →
`parse_bound_chunk_response_v2` → `synthesize_chunk_results_v2` → `compute_readiness_decision_v2` →
redação do bundle. A chamada ao Router é substituída por resposta sintética válida (a engine a
consome como dado); reaquisição da PR e teto de rollout são do adapter. Resultado:
`readiness=ready`, 2 chunks, `bundle_sha256` idêntico ao caminho normal (EXP-FUNC). Não é só
`import app.agent_review`.

| Componente | Origem | Consumidor | Carga | Efeito necessário | Bytes / metadata | Autoridade existente | Candidato |
|---|---|---|---|---|---|---|---|
| `app`, `app.agent_review` + 31 submódulos, `app.common`, `app.common.strict_json` | commit `C` do toolrepo | driver/adapter | inicial, imports da engine | toda a lógica | 35 módulos-fonte | #319 (anchor) + hash-on-read | **S_G** (raiz importável `app`) |
| demais arquivos de `C` (scripts, tests, docs, lock) | `C` | produtor (lock); ninguém importa | — | fidelidade de G; lock | árvore completa | idem | **S_G** como dado; raízes importáveis restritas a `app` |
| `pydantic` (42 módulos), `annotated_types`, `typing_extensions`, `typing_inspection` | wheels do lock | engine | inicial + lazy (`pydantic._migration`, `plugin._loader`) | modelos/validação | fontes `.py` | lock ∈ `C` + RECORD | **S_D** |
| `pydantic_core._pydantic_core` (nativo) | wheel cp311 manylinux | pydantic | inicial | validação | `.so`; NEEDED: libgcc_s, librt, libpthread, libm, libc, ld-linux; sem RPATH/RUNPATH (verificado pelo produtor) | lock + RECORD + identidade/tags | **S_D**; carregado de memfd selado **no filho** via `/proc/self/fd/N` |
| `yaml` + `yaml._yaml` (Cython) | wheel PyYAML | profile loader | inicial (`yaml.cyaml`) | engine usa só `SafeLoader` (Python); `_yaml` é inicializado mas não usado | `.so`; cria `_cython_3_0_11`, `cython_runtime` | lock + RECORD | **S_D** (incluído por paridade; exclusão exigiria decisão explícita) |
| metadata de distribuição (`*.dist-info`) | wheels | `importlib.metadata` (pydantic plugin loader) | tardio, 1ª criação de validator | nenhum no percurso | presentes em S_D como dados | — | **recusado como canal**: E não expõe distribuições; plugins pydantic **não suportados** |
| interpretador CPython 3.11 (+ `libpython3.11.so.1.0`) | host (root-owned) | launcher | antes de tudo | — | `/usr/local/bin/python3.11` no experimento | ownership/DAC | **TCB** |
| stdlib (103 fonte, 27 ext, 41 builtin/frozen) | host (root-owned) | engine, bootstrap | inicial | — | `sys.base_prefix/lib/python3.11` | ownership/DAC | **TCB** |
| ld.so, glibc, libgcc_s | host (root-owned) | carga nativa | antes do `main` e no dlopen | — | ld.so.cache | ownership + launcher fecha `LD_*` | **TCB** |
| `site`, `.pth` (inclusive `_distutils_hack` da venv), `sitecustomize`, user site, `pyvenv.cfg` | venv/usuário | startup | antes do 1º import | nenhum | mutáveis same-UID | — | **recusado** (`-I -S`, sem venv) |
| `__pycache__/*.pyc` do checkout | escrito pelo caminho normal | import normal | substitui a fonte | nenhum | mutável | — | **recusado**: E compila de S (EXP-FUNC mostra `.pyc` plantado com fonte idêntica ao commit executando no caminho normal) |
| dados do target (profile YAML, artefatos, contratos) | checkout base do target | `external_path_ingress_v2` (#200-G4B) | tardio, 2 aberturas por arquivo (valida → relê por path) | entrada do review | por path | ingress declara same-UID fora de escopo | **fora de S; aberto (U4)** |
| diff, respostas do Router | API / Router | engine | por valor | entrada | — | adapter | **fora de S** (dado) |
| adapter do target, Router client | checkout base do target | `__main__` do processo | inicial | orquestração, rede | — | target | **fora de S; aberto (U1)** |
| brokers/supervisor (`__file__` para relançar) | `C` | executor de checks | fora do percurso | relançar por path | `BROKER_PATH_V2`, `SUPERVISOR_PATH_V2` | — | **não suportado no 1º corte** (sem `__file__`, sem subprocesso em E) |
| `git` (produtor) | host | produtor | captura | transporte | — | nenhuma para integridade (hash-on-read) | **dependência de disponibilidade**, não TCB de integridade |
| `pwd` (stdlib) | TCB | `zoneinfo._tzpath` → `sysconfig._getuserbase` → `expanduser` sem `HOME` (pilha registrada) | tardio | resolve home pelo passwd (NSS nativo, `/etc/passwd` root-owned) | — | — | TCB; mostra que o ambiente é entrada: `env={}` muda o caminho da stdlib, sem efeito no resultado (paridade) |

Subprocessos no percurso: **0**; aberturas em checkout/venv durante E: **0**. Ambas as contagens vêm
do audit hook do próprio bootstrap: veem só eventos em nível Python (`open`, `subprocess.Popen`,
`os.system`, `os.spawn`, `os.exec`, `os.posix_spawn`, `os.fork`, `pty.spawn`), não aberturas feitas
por código nativo, e valem sob **P**. Cada caminho é resolvido contra o cwd (`realpath`) e comparado
por componente com as raízes observadas (R2-5); o controle positivo `auditor_detects_relative_checkout_read`
mostra que uma leitura relativa do checkout é detectada. Limite: o evento de `os.open(..., dir_fd=)`
não traz o `dir_fd`.

### 6.2 Piso de TCB proposto

1. Kernel Linux (memfd seals, `/proc`, Yama `ptrace_scope ≥ 1`) e root.
2. Interpretador CPython da versão/ABI dos wheels (`cp311`, manylinux glibc x86_64), stdlib,
   `lib-dynload`, ld.so/glibc/libgcc_s: **não graváveis pelo UID do runner**, invocados por caminho
   absoluto. Autenticá-los (estilo ADR-0012) é endurecimento futuro, não o 1º corte.
3. Launcher + produtor + bootstrap (a própria implementação de S/E): vindos de local não gravável
   pelo UID do runner; sem auto-autenticação recursiva. **Onde** isso vive em produção está aberto
   (U3).
4. Anchor `C`: #319.

"Fica no TCB" não é dispensa: cada item acima nomeia autoridade (ownership/DAC ou #319), forma de
aquisição (instalação root), mutabilidade (nenhuma pelo UID do runner), validação (EXP-BOOT
`floor`, com probe por arquivo — `access(W_OK)` e abertura `O_WRONLY` sem escrita — e por criação só
para diretórios; o probe é discriminado por um arquivo gravável dentro de diretório não gravável,
R2-6: 23 caminhos — interpretador, stdlib, `lib-dynload`, cada `.so` mapeado pelo interpretador,
as libs `NEEDED` do `pydantic_core` resolvidas pelo `ld.so.cache`, e todos os ancestrais — uid 0 e
escrita negada ao ator; **só neste container**) e consequência por target:

| Target | Situação observada | Consequência |
|---|---|---|
| AgentEscala | runner self-hosted `ct104`, sem `setup-python`; venv sobre o `python3` do host; ownership/versão **não observados** daqui | ativação depende de U2 e U3 |
| CAEM | sem workflow AgentReview v2 hoje | se usar `setup-python` em runner hospedado, o toolcache pertence ao UID do runner → piso 2 **não atendido**; não suportado até decisão |
| SACR-AS | sem workflow AgentReview v2 hoje | idem CAEM; fixtures clínicas sintéticas não mudam a engine |

## 7. Obrigações de desenho (IDs locais desta slice)

Formato: proposição · domínio · truth-maker · produtor → consumidor · contramodelo · positivo ·
limite · owner · evidência.

| Grupo | Obrigações | Estado em S0 |
|---|---|---|
| 7.1 — S_G (claim de S0) | 301S-ID, AUTH, FID, STAB, BIND, RES (commit/tree/blob), LIFE | a qualificar nesta PR |
| 7.2 — E (viabilidade) | 301S-BOOT, LOAD (confinamento observado), CHAN, DATA, CALLER | propostas; E é futura |
| 7.3 — S_D (futura slice) | 301S-DEP, NAT, RES (S_D), completude do loader | `DEFINED`; contramodelos obrigatórios R2-1/2/3/7/8 |

### 7.1 — S_G (claim de S0)

- **301S-ID** — S carrega `subject_identity = (algoritmo, C)`, `container_digest` e (para S_D)
  `dependency_identity` como fatos distintos; o algoritmo aceito é o implicado pelo `C` **esperado**,
  não o rótulo do container · captura/handoff · cabeçalho selado + digest · produtor → bootstrap ·
  bytes certos e commit errado; commit certo com rótulo de algoritmo incoerente (R2-4) · identidade
  correta aceita · — · #301 · EXP-CAPTURE `binding_right_bytes_wrong_subject_identity`,
  `binding_right_commit_wrong_algorithm_label`.
- **301S-AUTH** — todo objeto que contribui para S é autenticado no consumo · commit/tree/blob,
  sha1 e sha256 · hash-on-read sobre o buffer usado · produtor · troca de objeto commit, tree ou
  blob após a aquisição · bytes legítimos aceitos; restaurar volta a aceitar · pack/alternates só
  por argumento · #301 · EXP-N1 (21 casos, inclui ablação e testemunha herdada).
- **301S-FID** — S preserva as distinções de contract A/A2 e recusa o resto explicitamente ·
  árvores Git suportadas por C3 · paridade com árvore declarada **e** com o enumerador C3 ·
  produtor · 7 contramodelos (diretório vazio ±, aninhado, exec bit, target de symlink, tipo,
  vazio→arquivo) · paridade exata · nomes não-UTF-8 aceitos como bytes pelos dois · C3 (regra) /
  #301 (uso) · EXP-STRUCT (mutante com perda colide em 5/7). O protótipo **espelha** regras que o
  C3 já possui no builder hierárquico (duplicado, round-trip `fsdecode/fsencode`, ciclo,
  profundidade ≤ 100, componente ≤ 255, limite pré-leitura `size // 295`); S1 deve obtê-las do
  próprio C3, não dessas cópias.
- **301S-STAB** — após compromisso, S é imutável para não-root · memfd; observado só no kernel
  6.18/WSL2 · selos + re-hash
  pós-selo · produtor → todos · bateria de 9 operações de escrita por outro processo; escritor
  pré-selo; selo estranho; mapeamento gravável retido · conteúdo idêntico, compromisso sem atacante
  · disponibilidade não coberta · #301 · EXP-CAPTURE.
- **301S-BIND** — o consumidor só aceita o descritor recebido se selado, regular, com digest e
  identidade esperados vindos do launcher · handoff · `open_sealed` sobre o buffer único ·
  launcher → bootstrap · arquivo regular idêntico, memfd atacante, sem `F_SEAL_WRITE`, sem identidade
  esperada, fd não herdado · handoff correto · número de FD em receipt não é prova · #301 ·
  EXP-CAPTURE `binding_*`.
- **301S-RES (S_G)** — a captura de S_G é limitada antes da expansão · por ocorrência, para
  commit, tree **e** blob · todo corpo é cobrado pelo header antes de ser lido (commit: teto próprio;
  tree: limite pré-leitura do C3 com o orçamento restante e orçamento de metadados; blob: orçamento
  de payload) · produtor · subárvore compartilhada 2.000× (125 MiB por ocorrência de 64 KiB únicos);
  blob de 32 MiB; tree de 16,7 MB; commit de 16 MiB; profundidade 110; contagem cumulativa de nós ·
  corpus real aceito · limites propostos (§8) ainda não adjudicados; o orçamento cumulativo de nós
  é imposto pelo cap por árvore do C3 alimentado com o **restante** (o `budget_nodes` próprio é
  redundante) · #301 · EXP-RES.
- **301S-LIFE** — cada descritor tem um dono; falhas não produzem S parcial; filho sem resposta não
  vira sucesso · produtor/launcher · FD/processos contados antes e depois · — · falha de escrita
  (EFBIG), de selo (injetada), de hash no meio da captura, falha do launcher antes do spawn, filho
  travado, filho com rc 0 sem resposta · FDs restaurados, git reapado, ambos os lados do socketpair
  fechados · #354 não é ativado (§8) · #301 · EXP-RES.

### 7.2 — E (viabilidade; E é futura)

- **301S-BOOT** — nenhuma configuração controlada por outro ator age antes do 1º import ·
  launcher · interpretador root-owned absoluto, `env={}`, `-I -S`, `cwd=/` · launcher · `pyvenv.cfg`
  editado (executa sob `-I -S`), `LD_PRELOAD`, `PYTHONPATH`, módulo no cwd, `.pth` de user site ·
  controles sem payload · observação por marcador externo, não autorrelato · #301 · EXP-BOOT.
- **301S-LOAD** — semântica de import explícita · E · top-level: S só para suas raízes {`app`},
  antes do `PathFinder` (espelha `PYTHONPATH`); S_D depois (stdlib precede site-packages).
  Submódulos: cada finder só responde dentro de pacotes que ele próprio criou (o `__path__` desses
  pacotes contém apenas o marcador do finder), como o `__path__` confina o `FileFinder`. Ordem por
  nome: diretório com `__init__.py` > extensão (só D) > fonte `.py` > namespace PEP 420 de nó
  `tree`. Symlink em S é dado, **não** caminho de import; nenhum `.pyc` lido/escrito; nenhuma
  distribuição visível · bootstrap · fallback a checkout/venv/pyc; D tentando estender `app`,
  `json`, `encodings` ou sombrear `json` · 0 aberturas em checkout/venv; mesmo resultado; injeções
  recusadas · **não suportado, explicitamente**: `__file__`, `inspect.getsource`,
  `importlib.resources`, `pkgutil.iter_modules` sobre pacotes de S/D (a engine não os usa hoje;
  proibir um caso necessário exige decisão de produto) · #301 · EXP-FUNC, EXP-CAPTURE.
- **301S-CHAN** — o resultado só chega por canal não reabrível por terceiros · socketpair herdado ·
  ENXIO ao reabrir via `/proc` · bootstrap → launcher · pipe reaberto injeta resultado forjado ·
  resultado genuíno · o conteúdo ainda é autorrelato do filho (vale sob **P**) · #301 · EXP-PROC.
- **301S-DATA** (aberta) — dados do target consumidos por E não retornam a bytes mutáveis · hoje
  #200-G4B relê por path e declara same-UID fora de escopo · owner a decidir (#301-E / #331) · U4.
- **301S-CALLER** (aberta) — onde roda o adapter do target e como o Router é chamado sem pôr
  código do target no processo de E · owner #301-E · U1.

### 7.3 — S_D (futura slice; `DEFINED`, não qualificadas por S0)

Contramodelos **obrigatórios** herdados da rodada 2 (PR #355, comentário 5852251027):
- R2-1: `METADATA`/`WHEEL`/`RECORD` inflados antes da cobrança (reproduzido: 2.282 MiB / 1.001 MiB com
  orçamento de 8 MiB);
- R2-2: arquivo do wheel lido inteiro sem limite;
- R2-3: `DT_FILTER`/`DT_AUXILIARY` não inspecionados;
- R2-7: pacote-extensão `pkg/__init__.so` não procurado;
- R2-8: superconjunto de tags no `WHEEL` aceito.

Mecanismo proposto (spike descartável, não implementado):
[`experiments/sd_future/spike_bounded_archive.py`](experiments/sd_future/spike_bounded_archive.py) —
um único leitor de arquivo com cobrança pelo qual passa toda leitura. Reprodução do defeito no
protótipo congelado: [`experiments/sd_future/repro_r2.py`](experiments/sd_future/repro_r2.py). O
protótipo `s0_deps.py` fica **congelado** no estado de `1e2453e`, com esses defeitos conhecidos.

- **301S-DEP** — dependências vêm só de S_D, vinculado ao lock (nó regular) dentro de S_G · lock
  atual · sha256 do wheel ∈ lock, identidade interna e tags, RECORD, tamanho antes de inflar,
  colisões, vínculo `sha256(lock)` e tag do interpretador no cabeçalho · produtor → finder de E ·
  25 casos sintéticos (renomeado, tags, ABI, duplicado, RECORD, `.pth`, `.data`, `..`, symlink,
  colisões, hash/lock ausentes, lock duplicado/marker/hash em comentário, zip bomb de 200 MiB) + D de
  outro lock, D ausente, venv adulterada, plugin plantado · controle sintético aceito e paridade
  funcional · a checagem do consumidor é de **rótulo** (digest do lock e tag); a autenticação por
  membro aconteceu no produtor (TCB) · futura slice S_D · EXP-DEPS e EXP-FUNC observam o protótipo;
  limitação conhecida R2-1/R2-2/R2-8.
- **301S-NAT** — extensões nativas executam só bytes de S_D, e sua closure dinâmica fica no TCB ·
  `.so` do lock · ELF inspecionado pelo produtor; memfd selado criado e re-hasheado **no filho**,
  `ExtensionFileLoader` em `/proc/self/fd/N` · produtor → bootstrap · `.so` com `RPATH`, `RUNPATH` ou
  `DT_NEEDED` absoluto · `.so` sem caminhos aceito; `pydantic_core` e `yaml._yaml` carregados de
  memfd · as sonames resolvem pelo `ld.so.cache` root-owned; `LD_*` fechado pelo launcher · futura
  slice S_D · EXP-DEPS `native_*`, EXP-FUNC census; limitação conhecida R2-3/R2-7.
- **301S-RES (S_D)** — ingestão de S_D limitada antes de qualquer expansão (arquivo, diretório
  central, todo membro inclusive metadados) · **REFUTADA** no protótipo em `1e2453e` (R2-1/R2-2) ·
  futura slice S_D, com o mecanismo do spike.

## 8. Recursos e lifecycle

Medidos no corpus real (`C = 9abcde64`) em CPython 3.11.16 / tmpfs (EXP-FUNC); números valem
**para este ambiente/corpus**, não são tetos universais (K06 de #324):

| Vetor | S_G | S_D | E (filho) |
|---|---|---|---|
| Entradas lógicas | 1.001 nós (206 tree, 776 regular, 19 exec, 0 symlink, 0 vazios) | 186 nós = 164 membros + diretórios | 35 módulos de S, 66 de S_D, 2 nativos |
| Bytes únicos vs por ocorrência | 8.642.394 / 8.660.242 (blobs); 52.807 B de corpos commit/tree | 9.700.542 (payload) | — |
| Bytes de paths (soma dos comprimentos) | 54.726 B | — | — |
| Container (RAM-backed) | 8.769.133 B | 9.718.908 B | 2 memfds nativos no filho |
| Heap de pico do produtor | 25,9 MiB (build → selo) | 45,6 MiB | maxrss 87.216 KiB (normal: 78.076) |
| FDs | 1 por container | 1 | 3 herdados (por construção) + 1 socket + 2 nativos |
| Subprocessos | 2 `git` (contados: object format + cat-file) | 0 | 0 (audit hook) |
| I/O | 1.003 objetos, 8.713.049 B lidos do git | 6 wheels lidos 1× | 0 aberturas em checkout/venv |
| Tempo | build 0,164 s; serialize+selo+verificação 0,028 s | 0,096 s | 0,41 s (normal 0,26 s) |

Limites **propostos** (para adjudicação), aplicados antes da expansão: `max_payload_bytes` (blobs,
por ocorrência) = 64 MiB (≈7× o corpus atual; memfd é RAM/shmem, então o orçamento de disco de C3
de 2 GiB **não** se transfere); `max_metadata_bytes` (corpos commit+tree) = 64 MiB;
`max_commit_bytes` = 1 MiB; `max_path_bytes` = 16 MiB. Para S_D, um teto de 64 MiB é **proposto e
não imposto** pelo protótipo (R2-1/R2-2): fica com a futura slice e o mecanismo do spike. Valores que pertencem ao C3 e devem vir dele: entradas 100.000, profundidade 100,
componente 255, limite pré-leitura `size // 295`. Todo corpo é cobrado pelo header do `cat-file`
antes de ser lido (EXP-RES: blob de 32 MiB, tree de 16,7 MB e commit de 16 MiB recusados com heap
de 0,06 MiB; EXP-DEPS: um membro-bomba comum de 200 MiB é recusado com 0,27 MiB, mas `METADATA`/`RECORD`-bomba não —
R2-1). O memfd é contabilizado como shmem no memcg do processo;
comportamento sob limite de memcg **não testado**.

Ownership: produtor possui o memfd até retornar; em falha fecha uma vez e propaga. Launcher possui
as cópias herdadas até o filho terminar; o filho possui os memfds nativos que cria. Resultado ausente
(timeout, rc 0 sem resposta, canal malformado) nunca é convertido em resultado válido.

**#354.** Os sítios com a forma `open(next, dir_fd=cur)`/`close(cur)` estão em
`_open_directory_relative_v2` (G1/Q) e, em C3, em `_fd_rmtree` e na materialização
(`git_commit_subject_v2.py` ~300, ~1516, ~1828). O percurso de S usa `_parse_tree_data` (puro) e
`git cat-file`; bootstrap e launcher não abrem diretórios. **S não ativa #354.** Se uma
implementação futura reutilizar materialização ou a verificação Q no percurso de E, #354 passa a ser
obrigação do consumidor.

## 9. Interface conceitual S → E (sem implementar E)

```text
entrada admitida (C via #319, lock ∈ C) + política (orçamentos, raízes) + ambiente (piso §6.2)
  → captura autenticada (301S-AUTH/FID/RES)            [pode falhar; nada sai]
  → compromisso: S_G, S_D selados + digests            [301S-STAB]
  → handoff: descritores herdados (pass_fds) + identidade esperada no argv do launcher
             (digests, C) + socketpair de resultado; interpretador root-owned absoluto, env={}, -I -S
  → validação no bootstrap: regular ∧ selado ∧ digest ∧ (algoritmo, C) ∧ vínculo S_D↔lock   [301S-BIND/ID]
  → carga: finder fechado sobre S_G/S_D; nativos → memfd selado no filho       [301S-LOAD/NAT]
  → resultado pelo socketpair; ausência ≠ sucesso                              [301S-CHAN/LIFE]
```

- **Vínculo S_D ↔ S_G no consumidor é de rótulo**: confere `sha256(lock)` e a tag do interpretador
  gravados no cabeçalho selado de S_D; a autenticação por membro foi feita pelo produtor, sob a
  premissa de TCB do launcher.
- **Descritor recebido ≠ número de FD num receipt.** O que o consumidor validou é o objeto que o
  kernel entregou; um número escrito num receipt não identifica objeto algum depois do close.
- **Origem das observações.** Fatos que o launcher produziu (digest calculado, fds passados, argv,
  env, interpretador) são dele. O que o bootstrap observa (módulos por origem, aberturas, dlopen)
  vale sob **P** e termina a regressão no piso de §6.2. PID, `module.__file__`, `sys.flags` e
  mensagens do filho **não** provam o próprio bootstrap (ADR-0012 como referência; EXP-BOOT mostra
  código rodando antes do `-c` enquanto `sys.flags` seguiria correto).
- **Symlink preservado ≠ symlink importável.** S_G preserva symlinks como dados; o loader recusa
  importar através deles. Namespace packages vêm de nós `tree`. Relançamento de processos a partir
  de E e `importlib.resources` não fazem parte do 1º corte e exigirão semântica própria.
- **Não reconstruir S a partir de M.** E nunca abre checkout, venv ou M.

Perguntas **abertas que bloqueiam E** (não S1):

- **U1 — posição do adapter/Router.** No caller real, código base-owned do target é `__main__` do
  processo da engine e chama a rede no meio do pipeline. Opções: (a) E expõe fases da engine com
  E/S só de dados (build → Router fora de E → consume); (b) o adapter vira um segundo subject
  autenticado a partir do commit base do target. Decisão de produto; #301-E.
- **U2 — interpretador nos runners.** Versão/ABI/ownership do `python3` de CT104 não observados;
  CAEM/SACR-AS sem workflow. Sem isso o piso §6.2-2 é premissa, não fato.
- **U3 — proveniência do launcher/produtor/bootstrap.** Hoje a única cópia da engine no runner é o
  checkout gravável pelo UID do runner; o código que constrói S não pode vir dele. Exige instalação
  root-owned ou equivalente; pode interseccionar #331 (autoridade de storage do host).
- **U4 — canal de dados do target.** `external_path_ingress_v2` relê por path (2 aberturas por
  arquivo) e exclui same-UID do seu escopo. E precisa receber esses dados como bytes/descritor ou
  declarar a exclusão.

## 10. Alternativas consideradas

- **F — filho autentica direto do repositório antes do import** (spike): rejeitada; move parse
  Git para o TCB pré-import e mistura WHAT/HOW sem ganho, pois o launcher é TCB de qualquer forma.
- **memfd por arquivo:** rejeitada; ~800 FDs para o corpus atual contra 1 por container.
- **Alternativa material comparada — dependências por instalação root-owned (DAC) em vez de S_D.**
  Resolve 301S-DEP se o runner for provisionado por root e a venv não for gravável pelo UID do
  runner; perde o vínculo criptográfico `lock ∈ C → wheel → membro` (autoridade passa a ser o
  provisionamento, não o commit) e não fecha plugins/`.pth` se `site` rodar. S_D fica como proposta;
  a alternativa é compatível como piso de TCB se U3 for resolvido por provisionamento.
- **Autenticar também interpretador/stdlib (ADR-0012 `python-runtime` snapshot):** endurecimento
  posterior; exige launcher nativo e namespace de montagem; fora do 1º corte.

## 11. Respostas ao preflight §§1–7

1. **Propriedade.** S_G-CLAIM (§2). Observação mecânica: hash-on-read por objeto; cobrança de todo
   corpo pelo header; paridade estrutural; `F_GET_SEALS` + re-hash pós-selo; validação do descritor
   recebido e da identidade `(algoritmo, C)`. Paridade funcional e 0 aberturas em checkout/venv são
   viabilidade de E, não parte da claim. Disposição conservadora: qualquer falha recusa e
   **nenhum** S parcial é emitido; resultado ausente não é sucesso.
2. **Autoridade.** Formato de objeto Git (hash por tipo/tamanho/conteúdo) → S **deriva** dele.
   Regras de árvore → C3: `_parse_tree_data` (usada diretamente pelo protótipo) e o builder
   hierárquico `_build_canonical_trie_hierarchical` (duplicado, round-trip, ciclo, profundidade 100,
   componente, limite pré-leitura). O protótipo **espelha** estas últimas — são cópias, portanto hoje
   há duas instâncias dessas regras no protótipo; S1 deve ter **uma**: inserir a verificação de hash
   no leitor de objetos do C3 (`_list_single_tree_entries_v2` lê o corpo e parseia sem hashear) e
   consumir o builder do C3, sem enumerador paralelo. Autorização de wheels → lock em `C`; formato
   de wheel → PEP 427/RECORD/METADATA/WHEEL (derivados). Anchor → #319.
3. **Linguagem/capacidade.** Aceita: objetos sha1/sha256; modos `040000/100644/100755/120000`;
   nomes em bytes; wheels puros e nativos cp311 manylinux sem RPATH/RUNPATH. Recusa explícita:
   gitlink, modos não canônicos, `..`, duplicados, lock não regular, lock com duplicado/marker,
   identidade/tag de wheel divergente, `.pth`, `.data/`, symlink em wheel, colisão arquivo/diretório,
   nativo com caminho de busca, plugins pydantic, import através de symlink, `__file__`,
   `inspect.getsource`, `importlib.resources`, `pkgutil.iter_modules`, relançamento por path.
   U1–U4 são perguntas de E; U3 também condiciona o **significado operacional** de S1 (§12).
4. **Corpus.** Negativo: EXP-N1, EXP-STRUCT §4, EXP-CAPTURE, EXP-BOOT, EXP-RES. Positivo **com
   igualdade**: paridade com árvore declarada, com C3 (`list_commit_tree_structure_v2` +
   `read_commit_blobs_v2`) e com o resultado da engine no caminho normal. Limite: corpus sintético
   pequeno + o próprio toolrepo em `C`.
5. **Evidência/mutação.** Mutantes executados e observados: verificação de hash desligada
   (aceita blob adulterado), hash-então-relê (incorpora outros bytes), sem re-hash pós-selo
   (compromete conteúdo adulterado), projeção com perda (colide em 5/7). O finder anterior (head
   `3d426e1`, que ignorava `path`) funciona como mutante de 301S-LOAD: a revisão reproduziu nele a
   injeção que o finder corrigido recusa. Predicados admitidos (vocabulário do preflight):
   `DEFINED`, `MECHANICALLY_VERIFIED` e `EMPIRICALLY_SUPPORTED` no domínio/corpus declarados;
   `MUTATION_DISCRIMINATED` para 301S-AUTH/FID/STAB; 301S-RES (S_G) e 301S-ID só
   `MECHANICALLY_VERIFIED` no corpus declarado (sem mutantes executados). S_D: `DEFINED`; 301S-RES
   (S_D) é `REFUTED` no protótipo `1e2453e` (R2-1). Os instrumentos de medição também são
   discriminados: a auditoria de aberturas e o probe do piso têm controles positivos que os métodos
   da rodada 2 teriam falhado (R2-5, R2-6). `PROVED` não.
6. **Premissas entre camadas.** "git serve os bytes do oid" → **falso** (EXP-N1 HOR), por isso
   hash-on-read. "`-I -S` isola o startup" → falso para `pyvenv.cfg`/`LD_PRELOAD` (EXP-BOOT), por
   isso interpretador root-owned + `env={}`. "O lock autentica a venv" → falso (EXP-FUNC). "Yama
   impede ptrace" → verdadeiro neste host (`ptrace_scope=1`), premissa em CT104 (U2).
7. **Snapshot/ownership.** Uma decisão, um buffer: cada objeto é hasheado e incorporado da mesma
   leitura; o consumidor valida e parseia o mesmo `pread`. Nenhum outro consumidor relê M em E.
   Segunda cópia de regra: checagem de nome duplicado (acima). Dados do target: **duas leituras por
   path** (U4) — fora de S, registrado.

**Unknown material:** nenhum fato desconhecido para a claim de S_G nem para a propriedade de
componente de S1; U3 condiciona o que o resultado de S1 significa em operação e está declarado como
precondição (§12). U1–U4 bloqueiam E/ativação; S_D tem obrigações `DEFINED` e owner futuro.

## 12. Handoff — menor implementação seguinte

**S1 — captura autenticada e selada do subject Git (WHAT apenas).**

- Entradas: `repo_root` (locator), `C` esperado (40/64 hex), orçamentos.
- Saída: capability com memfd selado de `S_G` + `(algoritmo, C, root_tree, container_digest)`;
  função pura de parse/validação do consumidor sobre um descritor recebido.
- Consumidor previsto: o futuro launcher de E; nenhum caller de produção nesta slice.
- Write-set esperado: um módulo novo em `app/agent_review/` (captura + container + validação),
  verificação de hash inserida no leitor de objetos de C3 (sem enumerador paralelo; nome duplicado
  no dono), testes em `tests/agent_review/` portando EXP-N1/STRUCT/CAPTURE/RES e o corpus CM-333
  aplicável.
- Aceite: todos os casos das famílias N1/STRUCT/CAPTURE/RES com os mesmos contramodelos e mutantes;
  paridade com C3 no corpus; orçamentos adjudicados aplicados antes da expansão; FDs e processos
  lineares em toda falha; nenhum campo de wire novo sem decisão.
- Falsificadores: objeto adulterado aceito; nó de contract A perdido; S parcial retornado;
  escrita pós-compromisso; leak de FD/processo.
- Teto: sem launcher, sem loader, sem S_D, sem receipt, sem G5; **novo grant necessário**.
- **Precondição declarada (U3):** a propriedade de S1 é de componente — "dado um processo produtor
  íntegro, S_G é autêntico e estável". Como a cópia implantada de `app/agent_review/` hoje é o
  checkout gravável pelo UID do runner, nenhum consumidor pode tratar a saída de S1 como proteção de
  #301 até U3 ser resolvida. S1 não deve ser apresentado como "#301 protegido".
- **Decisões de adjudicação necessárias antes do grant de S1** (não são fatos desconhecidos):
  (i) valores dos orçamentos de §8; (ii) aceite do owner de C3 para inserir verificação de hash e
  novas razões de recusa no leitor de objetos do C3 (muda comportamento do C3 para todos os seus
  consumidores).

Cortes seguintes derivados desta arquitetura (não é sequência universal): **slice S_D** — leitor
único com cobrança (spike), identidade/tags/ELF e completude do loader, com R2-1/2/3/7/8 e o corpus
de `exp_deps.py` como contramodelos obrigatórios (depende de U2); launcher/E (depende de U1, U3; U4 decide se dados entram); receipt de execução
(depois de E). Nenhum deles fecha #301 sozinho; G5 (#350) e a composição operacional ficam com seus
owners. Outros owners inalterados: #319 (anchor), #331 (storage do host), #354 (transferência de
descritor), #298, #314, #350.
