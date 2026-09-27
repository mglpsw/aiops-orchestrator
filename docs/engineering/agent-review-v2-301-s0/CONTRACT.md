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

## 2. Claim proposta de S (≤ 3 frases) e non-claims

> **S-CLAIM.** Dado um commit esperado `C` fornecido por autoridade externa (#319) e o lock contido
> em `C`, um S admitido é o par de objetos selados `(S_G, S_D)` cujos bytes foram todos
> autenticados **no instante da leitura** — contra `C` pela cadeia commit → tree → blob no formato
> de objeto de `C`, ou contra os sha256 do lock autenticado — e que codifica sem perda as
> distinções da contract A/A2 de C3 sobre a árvore de `C`. Depois do instante de compromisso (selos
> `F_SEAL_{WRITE,GROW,SHRINK,SEAL}` lidos de volta e conteúdo selado re-hasheado igual ao digest dos
> bytes autenticados), nenhum processo sem `CAP_SYS_ADMIN`/root, inclusive do mesmo UID, altera
> bytes, índice ou tamanho de `S_G`/`S_D`. E recebe S por descritores herdados e a identidade
> esperada por canal separado controlado pelo launcher, valida selos e digest sobre o mesmo buffer
> que consome e não lê checkout, venv ou M.

Premissas (não impostas por S): kernel Linux com memfd seals; root e kernel confiáveis; `C` correto
(#319); launcher/produtor/bootstrap vindos de local não gravável pelo UID do runner (§6).

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
| Interferência no processo (ptrace, `/proc/pid/mem`) | com Yama `ptrace_scope ≥ 1`, não-ancestral não faz attach nem escreve memória; lista FDs enquanto dumpable | **P** somente sob a premissa Yama≥1 ∧ ator não-ancestral (EXP-PROC) | ancestrais (runner/agent) são confiados; `ptrace_scope=0` quebra **P** |
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
| `pydantic_core._pydantic_core` (nativo) | wheel cp311 manylinux | pydantic | inicial | validação | `.so`; NEEDED: libgcc_s, librt, libpthread, libm, libc, ld-linux; sem RPATH | lock + RECORD | **S_D**; carregado de memfd selado **no filho** via `/proc/self/fd/N` |
| `yaml` + `yaml._yaml` (Cython) | wheel PyYAML | profile loader | inicial (`yaml.cyaml`) | engine usa só `SafeLoader` (Python); `_yaml` é inicializado mas não usado | `.so`; cria `_cython_3_0_11`, `cython_runtime` | lock + RECORD | **S_D** (incluído por paridade; exclusão exigiria decisão explícita) |
| metadata de distribuição (`*.dist-info`) | wheels | `importlib.metadata` (pydantic plugin loader) | tardio, 1ª criação de validator | nenhum no percurso | presentes em S_D como dados | — | **recusado como canal**: E não expõe distribuições; plugins pydantic **não suportados** |
| interpretador CPython 3.11 | host (root-owned) | launcher | antes de tudo | — | `/usr/local/bin/python3.11` no experimento | ownership/DAC | **TCB** |
| stdlib (103 fonte, 27 ext, 41 builtin/frozen) | host (root-owned) | engine, bootstrap | inicial | — | `sys.base_prefix/lib/python3.11` | ownership/DAC | **TCB** |
| ld.so, glibc, libgcc_s | host (root-owned) | carga nativa | antes do `main` e no dlopen | — | ld.so.cache | ownership + launcher fecha `LD_*` | **TCB** |
| `site`, `.pth` (inclusive `_distutils_hack` da venv), `sitecustomize`, user site, `pyvenv.cfg` | venv/usuário | startup | antes do 1º import | nenhum | mutáveis same-UID | — | **recusado** (`-I -S`, sem venv) |
| `__pycache__/*.pyc` do checkout | escrito pelo caminho normal | import normal | substitui a fonte | nenhum | mutável | — | **recusado**: E compila de S (EXP-FUNC mostra `.pyc` plantado com fonte idêntica ao commit executando no caminho normal) |
| dados do target (profile YAML, artefatos, contratos) | checkout base do target | `external_path_ingress_v2` (#200-G4B) | tardio, 2 aberturas por arquivo (valida → relê por path) | entrada do review | por path | ingress declara same-UID fora de escopo | **fora de S; aberto (U4)** |
| diff, respostas do Router | API / Router | engine | por valor | entrada | — | adapter | **fora de S** (dado) |
| adapter do target, Router client | checkout base do target | `__main__` do processo | inicial | orquestração, rede | — | target | **fora de S; aberto (U1)** |
| brokers/supervisor (`__file__` para relançar) | `C` | executor de checks | fora do percurso | relançar por path | `BROKER_PATH_V2`, `SUPERVISOR_PATH_V2` | — | **não suportado no 1º corte** (sem `__file__`, sem subprocesso em E) |
| `git` (produtor) | host | produtor | captura | transporte | — | nenhuma para integridade (hash-on-read) | **dependência de disponibilidade**, não TCB de integridade |
| `pwd` (stdlib) | TCB | desconhecido (env vazio) | tardio | nenhum observado | — | — | TCB; chamador não atribuído, não material |

Subprocessos no percurso: **0** (audit hook). Aberturas em checkout/venv durante E: **0**.

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
aquisição (instalação root), mutabilidade (nenhuma pelo UID do runner), validação (stat/escrita
negada, EXP-BOOT floor) e consequência por target:

| Target | Situação observada | Consequência |
|---|---|---|
| AgentEscala | runner self-hosted `ct104`, sem `setup-python`; venv sobre o `python3` do host; ownership/versão **não observados** daqui | ativação depende de U2 e U3 |
| CAEM | sem workflow AgentReview v2 hoje | se usar `setup-python` em runner hospedado, o toolcache pertence ao UID do runner → piso 2 **não atendido**; não suportado até decisão |
| SACR-AS | sem workflow AgentReview v2 hoje | idem CAEM; fixtures clínicas sintéticas não mudam a engine |

## 7. Obrigações de desenho (IDs locais desta slice)

Formato: proposição · domínio · truth-maker · produtor → consumidor · contramodelo · positivo ·
limite · owner · evidência.

- **301S-ID** — S carrega `subject_identity`, `container_digest` e `dependency_identity` como fatos
  distintos · captura/handoff · cabeçalho selado + digest · produtor → bootstrap · handoff com
  bytes certos e commit errado · commit certo aceito · — · #301 · EXP-CAPTURE
  `binding_right_bytes_wrong_subject_identity`.
- **301S-AUTH** — todo objeto que contribui para S é autenticado no consumo · commit/tree/blob,
  sha1 e sha256 · hash-on-read sobre o buffer usado · produtor · troca de objeto commit, tree ou
  blob após a aquisição · bytes legítimos aceitos; restaurar volta a aceitar · pack/alternates só
  por argumento · #301 · EXP-N1 (21 casos, inclui ablação e testemunha herdada).
- **301S-FID** — S preserva as distinções de contract A/A2 e recusa o resto explicitamente ·
  árvores Git suportadas por C3 · paridade com árvore declarada **e** com o enumerador C3 ·
  produtor · 7 contramodelos (diretório vazio ±, aninhado, exec bit, target de symlink, tipo,
  vazio→arquivo) · paridade exata · nomes não-UTF-8 aceitos como bytes pelos dois · C3 (regra) /
  #301 (uso) · EXP-STRUCT (mutante com perda colide em 5/7).
- **301S-STAB** — após compromisso, S é imutável para não-root · memfd em Linux · selos + re-hash
  pós-selo · produtor → todos · bateria de 9 operações de escrita por outro processo; escritor
  pré-selo; selo estranho; mapeamento gravável retido · conteúdo idêntico, compromisso sem atacante
  · disponibilidade não coberta · #301 · EXP-CAPTURE.
- **301S-BIND** — o consumidor só aceita o descritor recebido se selado, regular, com digest e
  identidade esperados vindos do launcher · handoff · `open_sealed` sobre o buffer único ·
  launcher → bootstrap · arquivo regular idêntico, memfd atacante, sem `F_SEAL_WRITE`, sem identidade
  esperada, fd não herdado · handoff correto · número de FD em receipt não é prova · #301 ·
  EXP-CAPTURE `binding_*`.
- **301S-DEP** — dependências vêm só de S_D, vinculado ao lock dentro de S_G · lock atual ·
  sha256 do wheel ∈ lock, RECORD, vínculo `sha256(lock)` · produtor → finder de E · D de outro lock;
  D ausente (sem fallback); venv adulterada; plugin plantado · paridade funcional · wheels de outra
  plataforma exigem regenerar o lock · #301 · EXP-FUNC.
- **301S-NAT** — extensões nativas executam só bytes de S_D · `.so` do lock · memfd selado criado e
  re-hasheado **no filho**, `ExtensionFileLoader` em `/proc/self/fd/N` · bootstrap · — (coberto por
  301S-DEP) · `pydantic_core` e `yaml._yaml` carregados de memfd · bibliotecas de sistema resolvidas
  pelo ld.so do TCB; RPATH/`$ORIGIN` quebraria (não há hoje) · #301 · EXP-FUNC census.
- **301S-BOOT** — nenhuma configuração controlada por outro ator age antes do 1º import ·
  launcher · interpretador root-owned absoluto, `env={}`, `-I -S`, `cwd=/` · launcher · `pyvenv.cfg`
  editado (executa sob `-I -S`), `LD_PRELOAD`, `PYTHONPATH`, módulo no cwd, `.pth` de user site ·
  controles sem payload · observação por marcador externo, não autorrelato · #301 · EXP-BOOT.
- **301S-LOAD** — semântica de import explícita · E · raízes S = {`app`} antes do `PathFinder`
  (espelha `PYTHONPATH`); S_D depois (stdlib precede site-packages); pacote > módulo > extensão (só
  D) > namespace PEP 420 de nó `tree`; symlink em S é dado, **não** caminho de import; sem
  `__file__`; `get_source` servido de S; nenhum `.pyc` lido/escrito; nenhuma distribuição visível ·
  bootstrap · fallback a checkout/venv/pyc · 0 aberturas em checkout/venv; mesmo resultado · proibir
  um caso necessário exige decisão de produto · #301 · EXP-FUNC, EXP-CAPTURE.
- **301S-CHAN** — o resultado só chega por canal não reabrível por terceiros · socketpair herdado ·
  ENXIO ao reabrir via `/proc` · bootstrap → launcher · pipe reaberto injeta resultado forjado ·
  resultado genuíno · o conteúdo ainda é autorrelato do filho (vale sob **P**) · #301 · EXP-PROC.
- **301S-RES** — S e sua captura são limitados antes da expansão · por ocorrência · orçamento
  cobrado pelo header do objeto antes do corpo · produtor · subárvore compartilhada 2.000× (125 MiB
  por ocorrência de 64 KiB únicos); blob de 32 MiB com orçamento de 8 MiB; profundidade · corpus real
  aceito · limites propostos (§8) ainda não adjudicados · #301 · EXP-RES.
- **301S-LIFE** — cada descritor tem um dono; falhas não produzem S parcial; filho sem resposta não
  vira sucesso · produtor/launcher · FD/processos contados antes e depois · — · falha de escrita
  (EFBIG), de selo, de hash no meio da captura, filho travado, filho com rc 0 sem resposta · FDs
  restaurados, git reapado · #354 não é ativado (§8) · #301 · EXP-RES.
- **301S-DATA** (aberta) — dados do target consumidos por E não retornam a bytes mutáveis · hoje
  #200-G4B relê por path e declara same-UID fora de escopo · owner a decidir (#301-E / #331) · U4.
- **301S-CALLER** (aberta) — onde roda o adapter do target e como o Router é chamado sem pôr
  código do target no processo de E · owner #301-E · U1.

## 8. Recursos e lifecycle

Medidos no corpus real (`C = 9abcde64`) em CPython 3.11.16 / tmpfs (EXP-FUNC); números valem
**para este ambiente/corpus**, não são tetos universais (K06 de #324):

| Vetor | S_G | S_D | E (filho) |
|---|---|---|---|
| Entradas lógicas | 1.001 nós (206 tree, 776 regular, 19 exec, 0 symlink, 0 vazios) | 186 membros + diretórios | 35 módulos de S, 66 de S_D, 2 nativos |
| Bytes únicos vs por ocorrência | 8.642.394 / 8.660.242 | 9.700.542 (payload) | — |
| Heap de paths | 54.726 B | — | — |
| Container (RAM-backed) | 8.769.133 B | 9.718.902 B | 2 memfds nativos no filho |
| Heap de pico do produtor | 26,1 MiB (build → selo) | 45,6 MiB | maxrss 87.100 KiB (normal: 76.872) |
| FDs | 1 por container | 1 | 3 herdados + 1 socket + 2 nativos |
| Subprocessos | 2 `git` (object format + cat-file) | 0 | 0 |
| I/O | 1.003 objetos, 8.713.049 B lidos do git | 6 wheels lidos 1× | 0 aberturas em checkout/venv |
| Tempo | build 0,159 s; serialize+selo+verificação 0,025 s | 0,09 s | 0,40 s (normal 0,26 s) |

Limites **propostos** (para adjudicação), aplicados antes da expansão: `max_payload_bytes` por
ocorrência = 64 MiB (≈7× o corpus atual; memfd é RAM/shmem, então o orçamento de disco de C3 de
2 GiB **não** se transfere); `max_nodes` = 100.000 (C3); `max_depth` = 64 para todo tipo de nó;
`max_path_bytes` = 16 MiB; `max_component_len` = 255; cap por árvore = o de C3 (`_parse_tree_data`);
`S_D` ≤ 64 MiB. Cobrança pelo header do `cat-file` antes de ler o corpo (EXP-RES: recusa com
heap de 0,06 MiB para um blob de 32 MiB). O memfd é contabilizado como shmem no memcg do processo;
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
  → validação no bootstrap: regular ∧ selado ∧ digest ∧ C ∧ vínculo S_D↔lock   [301S-BIND/ID]
  → carga: finder fechado sobre S_G/S_D; nativos → memfd selado no filho       [301S-LOAD/NAT]
  → resultado pelo socketpair; ausência ≠ sucesso                              [301S-CHAN/LIFE]
```

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

1. **Propriedade.** S-CLAIM (§2). Observação mecânica: hash-on-read por objeto; paridade estrutural;
   `F_GET_SEALS` + re-hash pós-selo; validação do descritor recebido; paridade funcional e 0
   aberturas em checkout/venv. Disposição conservadora: qualquer falha recusa e **nenhum** S
   parcial é emitido; resultado ausente não é sucesso.
2. **Autoridade.** Formato de objeto Git (hash por tipo/tamanho/conteúdo) → S **deriva** dele.
   Regra de árvore → C3 `_parse_tree_data` (derivada, não reimplementada; a implementação deve
   inserir a verificação no leitor de objetos do C3 — `_list_single_tree_entries_v2` lê o corpo e
   parseia sem hashear — em vez de um enumerador paralelo). Autorização de wheels → lock em `C`.
   Anchor → #319. Autoridades para a regra de árvore: 1 antes, 1 depois. A checagem de nome
   duplicado do protótipo é uma segunda regra e deve migrar para o dono (C3) na implementação.
3. **Linguagem/capacidade.** Aceita: objetos sha1/sha256; modos `040000/100644/100755/120000`;
   nomes em bytes; wheels puros e nativos cp311 manylinux sem RPATH. Recusa explícita: gitlink,
   modos não canônicos, `..`, duplicados, `.pth`, `.data/`, symlink em wheel, plugins pydantic,
   import através de symlink, `__file__`, relançamento por path. Implícito restante: nenhum
   conhecido; U1–U4 são perguntas de E, não de S.
4. **Corpus.** Negativo: EXP-N1, EXP-STRUCT §4, EXP-CAPTURE, EXP-BOOT, EXP-RES. Positivo **com
   igualdade**: paridade com árvore declarada, com C3 (`list_commit_tree_structure_v2` +
   `read_commit_blobs_v2`) e com o resultado da engine no caminho normal. Limite: corpus sintético
   pequeno + o próprio toolrepo em `C`.
5. **Evidência/mutação.** Mutantes executados e observados: verificação de hash desligada
   (aceita blob adulterado), hash-então-relê (incorpora outros bytes), sem re-hash pós-selo
   (compromete conteúdo adulterado), projeção com perda (colide em 5/7). Predicados admitidos
   (vocabulário do preflight): `DEFINED`, `MECHANICALLY_VERIFIED` e `EMPIRICALLY_SUPPORTED` no
   domínio/corpus declarados, `MUTATION_DISCRIMINATED` para 301S-AUTH/FID/STAB; `PROVED` não.
6. **Premissas entre camadas.** "git serve os bytes do oid" → **falso** (EXP-N1 HOR), por isso
   hash-on-read. "`-I -S` isola o startup" → falso para `pyvenv.cfg`/`LD_PRELOAD` (EXP-BOOT), por
   isso interpretador root-owned + `env={}`. "O lock autentica a venv" → falso (EXP-FUNC). "Yama
   impede ptrace" → verdadeiro neste host (`ptrace_scope=1`), premissa em CT104 (U2).
7. **Snapshot/ownership.** Uma decisão, um buffer: cada objeto é hasheado e incorporado da mesma
   leitura; o consumidor valida e parseia o mesmo `pread`. Nenhum outro consumidor relê M em E.
   Segunda cópia de regra: checagem de nome duplicado (acima). Dados do target: **duas leituras por
   path** (U4) — fora de S, registrado.

**Unknown material:** nenhum para S1 (§12). U1–U4 bloqueiam E/ativação e ficam nomeados acima.

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

Cortes seguintes derivados desta arquitetura (não é sequência universal): S_D + loader fechado
(depende de U2); launcher/E (depende de U1, U3; U4 decide se dados entram); receipt de execução
(depois de E). Nenhum deles fecha #301 sozinho; G5 (#350) e a composição operacional ficam com seus
owners. Outros owners inalterados: #319 (anchor), #331 (storage do host), #354 (transferência de
descritor), #298, #314, #350.
