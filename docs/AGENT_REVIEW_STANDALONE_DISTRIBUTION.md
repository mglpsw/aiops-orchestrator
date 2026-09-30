# AgentReview Standalone Distribution Closure (#351-B0)

**Status:** `CANDIDATE | B0 DISTRIBUTION CLOSURE — 4-layer proof architecture`<br>
**Owner:** `#351` (distribution & source separation track)  
**Roadmap:** `#46` (canonical roadmap authority)  
**Reference:** [`docs/CROSS_REPO_DISSOLUTION_MATRIX.md`](CROSS_REPO_DISSOLUTION_MATRIX.md)  

---

## 1. Objetivo e Proposição Central

Esta especificação e seu ferramental associado materializam a menor fatia executável de **#351-B — distribuição independente**, provando formal e deterministicamente a seguinte proposição:

> **AgentReview pode ser materializado, instalado, importado e exercitado em modo offline a partir de uma fronteira explícita de produto, sem depender do código-fonte ou das dependências exclusivas do AIOps Runtime.**

Esta fatia **não executa a separação física nem aposenta o AIOps Runtime**. Trata-se de uma **prova de separabilidade** (`SeparabilityProven != PhysicalMigrationExecuted`).

---

## 2. Arquitetura em 4 Camadas de Verificação

A verificação do B0 não tenta construir um resolvedor estático universal nem duplicar o gerenciador de pacotes do Python. A prova é particionada em **4 camadas ortogonais e auditáveis**:

```
+-------------------------------------------------------------------------+
| Layer S: Static Boundary Contract                                      |
|   - Manifest v1 com seções obrigatórias e âncoras positivas (T-01)      |
|   - Âncoras negativas obrigatórias de runtime (R-01)                    |
|   - Caminhos relativos canônicos sem traversal (T-03)                   |
|   - Proibição estrita de symlinks na fronteira (R-03)                   |
|   - Disjunção bidirecional entre fronteira positiva e negativa (U-05)  |
|   - Lint de imports diretos de módulos e pacotes proibidos (M4, T-02)   |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| Layer M: Safe Materializer                                              |
|   - Validação da Layer S antes de qualquer escrita                      |
|   - Disjunção estrita entre origem e destino antes da criação (U-04)   |
|   - Destino limpo: ausente ou vazio na identidade resolvida (T-05, L-01)|
|   - Confinamento da escrita estritamente a target_resolved (R-02)       |
|   - Proibição estrita de destinos symlink (R-02)                        |
|   - Cópia estrita dos membros declarados e fechamento de saída          |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| Layer E: Executable Isolation Evidence                                  |
|   - Subprocesso isolado com PYTHONPATH apontando APENAS ao standalone   |
|   - Origem dos módulos provada dentro do diretório standalone (M3)      |
|   - Módulos de runtime ausentes (ModuleNotFoundError não transitivo)    |
|   - Smoke de importação e funcionamento dos 9 módulos centrais          |
|   - Controles positivos executáveis v1, v2 e coexistência (C5, C6, C7)  |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| Layer I: Install Contract Evidence                                      |
|   - Autoridade única de instalação: requirements-agent-review.lock      |
|   - Suíte de testes dedicada: tests/agent_review/test_minimal_toolrepo_lock|
|   - Sem duplicação de autoridade entre AST estático e Lockfile          |
|   - Script canônico exige alvo venv ausente (elimina resíduos)          |
|   - Teste de hash adulterado valida integridade criptográfica (64-hex)  |
+-------------------------------------------------------------------------+
```

---

## 3. Fronteira Declarada do Produto

A fronteira do produto é formalizada e versionada na projeção canônica:  
[`config/agent-review/standalone-distribution-manifest.v1.json`](../config/agent-review/standalone-distribution-manifest.v1.json).

### Classificação Estrutural de Componentes

| Categoria | Superfície no Repositório | Justificativa e Papel |
|---|---|---|
| **AGENTREVIEW_CORE** | `app/agent_review/**`<br>`app/__init__.py` | Código-fonte do motor de revisão (linhas v1 e v2). |
| **AGENTREVIEW_ASSET** | `templates/agentreview-v2-target-pack/**`<br>`schemas/agent-review/v2/**` | Árvores de templates e esquemas JSON consumidas diretamente em tempo de execução/geração pelo `target_pack_build_v2.py`. |
| **AGENTREVIEW_INSTALL** | `requirements-agent-review.lock`<br>`scripts/install-agent-review-toolrepo.sh`<br>`docs/AGENT_REVIEW_V2_INSTALLATION.md` | Contrato canônico de instalação offline e independente do toolrepo sem dependências de banco ou runtime. |
| **SHARED_REQUIRED** | `app/common/strict_json.py` (+ `__init__.py`)<br>`app/services/environment_context.py` (+ `__init__.py`) | Primitivas compartilhadas estritamente necessárias. Importam apenas biblioteca padrão e PyYAML. |
| **DISTRIBUTION_CLIS** | 16 ferramentas operacionais de linha de comando (`scripts/agent-review-target-pack-v2.py`, `scripts/aiops-acquire-authoritative-checks-v2.py`, `scripts/aiops-review-*.py`, `scripts/export-agent-review-v2-schemas.py`, `scripts/github_agent_review.py`, `scripts/migrate-agent-review-profile-v1-v2.py`, `scripts/verify-agent-review-v2-conformance.py`). Todas ancoradas obrigatoriamente em `REQUIRED_DISTRIBUTION_CLIS_V1`. |
| **AGENTREVIEW_QUALIFICATION** | `tests/agent_review/**`<br>`tests/evals/**`<br>`evals/agent_review_v2/**`<br>`scripts/run-agent-review-v2-evals.py` | Ferramentas de qualificação, testes e benchmarks. Declaradas fora da distribuição operacional em runtime (`NeededToQualifyProduct != NeededAtRuntimeByProduct`). |
| **AIOPS_RUNTIME_ONLY (Forbidden)** | `app/main.py`<br>`app/api/**`<br>`app/agent_router/**`<br>`app/models/**`<br>`app/policies/**`<br>`app/adapters/**`<br>`app/services/orchestrator.py`<br>`deploy/**`<br>`config/actions.yaml` | Código exclusivo do orquestrador legado, banco de dados e rotas web. Totalmente ausente da distribuição standalone. |

### Invariantes de Dependências Compartilhadas

Para as primitivas compartilhadas:

```text
app/common/strict_json.py
app/services/environment_context.py
```

vigora rigorosamente o invariante:

```text
AgentReviewNeeds(X) != AgentReviewOwnsExclusively(X)
```

1. **Sem apropriação indevida:** O fato de o AgentReview necessitar de `strict_json` e `environment_context` não significa que o AIOps Runtime não os utilize mais.
2. **Sem acoplamento reverso:** Nenhuma dessas primitivas importa qualquer módulo do `app/` além de si mesmas.
3. **Decisão física postergada:** A decisão de mover, duplicar ou empacotar externamente essas primitivas pertence às fases subsequentes de migração física, não ao B0.

---

## 4. Fronteira Negativa e Política de Symlinks

### Superfícies Proibidas de Runtime
O manifesto declara explicitamente a lista canônica de 32 superfícies `forbidden_runtime_surfaces`, cuja presença e completude exata são obrigatórias (`REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1`). Nenhuma dessas superfícies pode ser incluída na distribuição nem importada por arquivos da mesma:

- **Núcleo e rotas de runtime:** `app/main.py`, `app/api`, `app/agent_router`, `app/models`, `app/policies`, `app/adapters`, `app/utils`
- **Serviços de runtime:** `app/services/orchestrator.py`, `app/services/provider_registry.py`, `app/services/task_service.py`, `app/services/action_planner.py`, `app/services/action_catalog.py`, `app/services/aiops_chat_router.py`
- **Subsistemas legados:** `app/caem_consumer`, `app/ri_b0a`, `app/projectops`
- **Infraestrutura e deploy:** `deploy`
- **Configurações de runtime:** `config/actions.yaml`, `config/policies.yml`, `config/providers.yml`, `config/routes.yml`
- **Scripts de runtime e ciclo de vida:** `scripts/aiops-runtime-backup-manifest.py`, `scripts/aiops-runtime-inventory.py`, `scripts/aiops-runtime-postcheck.py`, `scripts/backup.sh`, `scripts/rollback.sh`, `scripts/install.sh`, `scripts/smoke_test.sh`, `scripts/validate_actions_catalog.sh`, `scripts/validate_bluegreen.sh`, `scripts/compare_aiops_runtimes.sh`, `scripts/migrate_savings_to_sqlite.py`

### Pacotes e Raízes de Importação Proibidos de Runtime
O closure do AgentReview em modo standalone não requer nem importa pacotes pertencentes exclusivamente ao runtime:
- Distribuições do PyPI: `aiosqlite`, `asyncpg`, `duckduckgo-search`, `fastapi`, `httpx`, `psycopg`, `psycopg2`, `pydantic-settings`, `python-multipart`, `sqlalchemy`, `starlette`, `uvicorn`.
- Raízes de importação Python (projeção do contrato conhecido de dependências negativas `KNOWN_FORBIDDEN_RUNTIME_DEPENDENCIES_V1`): `aiosqlite`, `asyncpg`, `duckduckgo_search`, `fastapi`, `httpx`, `psycopg`, `psycopg2`, `pydantic_settings`, `multipart`, `python_multipart`, `sqlalchemy`, `starlette`, `uvicorn`. A checagem de AST (Layer S) valida contra as raízes de importação, distinguindo-as formalmente dos nomes de distribuição de pacotes sem reivindicar mapeamento universal AST -> PyPI (preservando B0-N2).

### Política de Symlinks Fail-Closed
Nenhum arquivo ou diretório declarado na distribuição pode ser um link simbólico, nem conter symlinks internos (`any sub.is_symlink() -> FAIL CLOSED`). Qualquer link simbólico detectado na validação ou na materialização causa rejeição imediata, eliminando deterministicamente riscos de ciclos, travessia de subárvores ou escapes do repositório. Da mesma forma, o diretório de destino da materialização não pode ser um symlink.

---

## 5. Validação e Materialização Determinística

O validador [`scripts/verify-agent-review-standalone-closure.py`](../scripts/verify-agent-review-standalone-closure.py) implementa as camadas estática e de materialização (Layers S e M):

1. **Validação de Schema e Âncoras (Layer S):** Valida `manifest_version`, seções obrigatórias e âncoras positivas/negativas (`REQUIRED_BOUNDARY_ANCHORS_V1` e `REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1`).
2. **Verificação de Caminhos e Symlinks (Layer S):** Assegura que todos os caminhos sejam canônicos, existam em disco, permaneçam confinados ao repositório e não contenham symlinks.
3. **Inspeção de AST (Layer S):** Analisa recursivamente todas as árvores de código Python da distribuição declarada e garante que nenhum import direto de módulo ou pacote proibido exista.
4. **Materialização Segura (Layer M):** A função `materialize_standalone_distribution(repo_root, target_dir)` valida o manifesto, verifica disjunção de origem/destino, assegura que o destino seja diretório vazio ou ausente na sua identidade canônica resolvida (`target_resolved`, nunca symlink), confina a criação e escrita estritamente a `target_resolved` (impedindo que caminhos não-canônicos com segmentos intermediários não-criados contornem checagens de limpeza), preserva a atestação imutável de identidade de fonte (`.source-commit` e `.toolrepo-sha`) para consumo pelo instalador, verifica o fechamento de saída e garante preservação estrita de write-zero quando acionado com `--check`.

Execução da verificação:

```bash
python3 scripts/verify-agent-review-standalone-closure.py --check
```

---

## 6. Provas Automatizadas e Matriz de Countermodels

A suíte [`tests/agent_review/test_standalone_distribution_closure.py`](../tests/agent_review/test_standalone_distribution_closure.py) e a suíte de instalação [`tests/agent_review/test_minimal_toolrepo_lock.py`](../tests/agent_review/test_minimal_toolrepo_lock.py) fornecem as evidências executáveis (Layers E e I):

### Controles Positivos

1. **Validação do Manifest:** O arquivo de configuração atende a todas as restrições da especificação.
2. **Closure de AST:** Zero violações em todos os módulos e scripts do AgentReview.
3. **Execução em Subprocesso Isolado:** A materialização standalone é executada com `PYTHONPATH` apontando exclusivamente para a raiz temporária. Nenhum módulo do AIOps Runtime consegue ser importado (`ModuleNotFoundError`).
4. **Smoke de Módulos Centrais:** Importação e verificação de origem em subprocesso isolado para os 9 módulos essenciais: `contracts_v2`, `schemas`, `semantic_chunker`, `authoritative_check_policy_v2`, `target_pack_build_v2`, `schema_export_v2`, `cli`, `strict_json` e `environment_context`.
5. **Controle Positivo v1:** Classificação determinística de arquivos e leitura de veredictos sem provedores externos.
6. **Controle Positivo v2:** Execução de `build_target_pack_manifest_v2` navegando pela árvore git dos `templates/` e `schemas/` dentro da raiz temporária standalone, gerando manifest e hashes válidos.
7. **Coexistência v1/v2:** Ambas as capacidades são exercitadas no mesmo ambiente materializado sem interferência mútua.

### Matriz de Countermodels e Controles de Fronteira

| Identificador | Descrição do Countermodel | Mecanismo de Bloqueio / Falha Causal |
|---|---|---|
| **M1** | Omissão de primitiva compartilhada | Falha causal direta (`ModuleNotFoundError`) nos módulos dependentes. |
| **M2** | Omissão de assets de templates | Falha imediata em `build_target_pack_manifest_v2` com `BUILD_TEMPLATE_ROOT_MISSING_REASON_V2`. |
| **M3** | Escape para checkout original | Detector de escape rejeita qualquer resolução cujo `__file__` aponte para fora do standalone. |
| **M4** | Injeção de dependência de runtime | Injetar pacote proibido (`import fastapi`) é detectado e bloqueado deterministicamente via AST. |
| **M5** | Violação do contrato de instalação | Omitir artefatos obrigatórios de instalação invalida deterministicamente o manifesto. |
| **M6** | Fronteira parcial v1 ou v2 | Exclusão de arquivos de qualquer perfil causa falha no controle positivo correspondente. |
| **T-01** | Não-vacuidade de seções centrais | Seções ausentes, vazias ou sem âncoras obrigatórias falham deterministicamente. |
| **T-02** | Confinamento de `ImportFrom` e `*` | Imports de submódulos proibidos (`from app import models`) e star imports ambíguos falham closed. |
| **T-03** | Confinamento de caminhos | Caminhos não canônicos, com traversal (`..`) ou que escapem do repo falham closed. |
| **T-04** | Rejeição de manifesto vazio | Manifestos explícitos vazios (`manifest={}`) são validados e rejeitados sem fallback. |
| **T-05** | Alvo limpo e fechamento de saída | Destino pré-existente não vazio é rejeitado; arquivos de saída conferem com o manifesto. |
| **U-01** | Imports relativos com traversal | `from .. import ...` que ultrapasse a raiz do pacote falha closed com `ValueError`. |
| **U-02** | Fronteira exata de pacote | Correspondência exata de componentes bloqueia pacotes irmãos (ex.: `app.agent_review_runtime`). |
| **U-03** | Confinamento de subárvores | Subárvores com links para fora ou para áreas proibidas falham closed na validação e cópia. |
| **U-04** | Disjunção estrita do destino | Destino aninhado em fontes ou coincidente com fontes falha antes de qualquer criação em disco. |
| **U-05** | Sobreposição bidirecional | Bloqueia ancestrais e descendentes de superfícies proibidas via `paths_overlap`. |
| **R-01** | Não-vacuidade da fronteira negativa | Seção `forbidden_runtime_surfaces` ausente, vazia ou sem âncoras obrigatórias falha closed. |
| **R-02** | Confinamento com alvo relativo/symlink | Suporta alvos relativos contra `target_resolved` e rejeita destinos symlink fail-closed. |
| **R-03** | Ciclos de symlink e links quebrados | Qualquer symlink em membros da distribuição falha closed na validação e materialização. |
| **F-01** | Não-vacuidade de pacotes proibidos | `dependency_closure.forbidden_runtime_packages` ausente, vazio ou sem âncoras (`REQUIRED_FORBIDDEN_RUNTIME_PACKAGES_V1`) falha closed. |
| **F-03** | Semântica de imports AST vs disco | Imports utilizam semântica exata ou descendente (`module_is_same_or_descendant`), permitindo imports legítimos de pacotes-pai (`import app`, `import app.services`) enquanto bloqueia submódulos de runtime (`app.models`). |
| **F-04** | Provas imunes a `PYTHONOPTIMIZE` | Probes em subprocesso utilizam helper explícito `require()`, e `_clean_env` expurga `PYTHONOPTIMIZE`, impedindo anulação de checagens sob flags de otimização. |
| **F-05** | Confinamento de origem por componentes | Verificação de origem de módulo utiliza `origin_path.is_relative_to(root)` em vez de prefixo de string, rejeitando diretórios irmãos (`/standalone-old`). |
| **F-02** | Composição Layer E × Layer I | Teste composto (`test_lock_built_venv_executes_materialized_standalone_agentreview`) exercita a árvore materializada com o interpretador criado a partir de `requirements-agent-review.lock` (`requires_network`). O instalador vincula estritamente o contrato canônico a CPython 3.11, recusando interpretadores incompatíveis fail-closed antes da criação do venv. Qualificado no ambiente canônico CPython 3.11 do repositório (GitHub Actions). |
| **H-01** | Isolamento do probe do interpretador | O probe de versão e implementação em `install-agent-review-toolrepo.sh` executa com flags isoladas/sem site (`-I -S`), provando que hooks de inicialização ambiental (`sitecustomize.py` via `PYTHONPATH`) não alteram a identidade reportada antes da criação do venv. |
| **H-02** | Contrato de import roots conhecidos | Distingue nomes de distribuição do PyPI (ex.: `pydantic-settings`, `duckduckgo-search`, `python-multipart`) de suas raízes de importação Python (`pydantic_settings`, `duckduckgo_search`, `multipart`), garantindo detecção precisa de dependências proibidas na Layer S sem reivindicar mapeamento universal AST -> PyPI. |
| **J-01** | Reutilização de venv pré-existente / contaminação residual | `scripts/install-agent-review-toolrepo.sh` exige alvo ausente (`[ -e "$VENV_DIR" ] || [ -L "$VENV_DIR" ]`), recusando fail-closed (código 2) reutilizar diretórios ou symlinks pré-existentes, impedindo a sobrevivência de artefatos obsoletos em `site-packages`. |
| **J-02** | Falso positivo no probe de ausência por falha transitiva | `missing_is_requested_module_or_parent` valida que a exceção `ModuleNotFoundError` corresponde ao módulo requisitado ou a seu ancestral, comprovando que módulos vazados com falhas transitivas (ex.: `app.main` importando dependência ausente) são rejeitados. |
| **J-03** | Falso positivo no teste de hash por erro sintático | `test_require_hashes_rejects_a_tampered_lock_file` altera exatamente 1 nibble mantendo 64 caracteres hexadecimais minúsculos (`[0-9a-f]{64}`), comprovando rejeição criptográfica pelo pip (`THESE PACKAGES DO NOT MATCH THE HASHES`) e não rejeição preliminar por sintaxe malformada. |
| **L-01** | Checagem de limpeza com path não-canônico | Caminho não-canônico com prefixo não-criado (`/tmp/new/../existing-target`) é resolvido antes da checagem; destino não-vazio pré-existente é recusado fail-closed e diretórios intermediários não são criados. |
| **L-02** | Sombra de venv e pip por `PYTHONPATH` ambiental | Criação do venv (`-I -S -m venv`) e execução do pip (`-I -m pip`) operam em modo isolado, provando imunidade contra shadowing por scripts `venv.py` ou pacotes `pip` presentes no `PYTHONPATH`. |
| **L-03** | Incompatibilidade de plataforma do lockfile antes da criação do venv | O probe isolado valida a plataforma completa (CPython 3.11, Linux, x86_64, glibc >= 2.17) antes de criar o venv, impedindo venvs parciais/quebrados em arquiteturas ou libcs incompatíveis (aarch64, musl, macOS). |
| **M-01** | Completude da fronteira negativa (32 superfícies canônicas) | `REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1` e manifesto canônico exigem paridade total com todas as 32 superfícies de runtime (incluindo `config/providers.yml`, `config/policies.yml`, `config/routes.yml` e scripts de runtime); omissão ou inserção na fronteira positiva falha closed. |
| **M-02** | Confinamento de origem por componentes no detector M3 | O detector executável de escape utiliza `Path.is_relative_to`, comprovando a rejeição tanto de escapes para o repo original quanto para diretórios irmãos que compartilham prefixo de string (`/standalone-old`). |
| **M-03** | Isolamento de configuração e variáveis do pip (`PIP_CONFIG_FILE=/dev/null` + `--isolated`) | O instalador invoca `PIP_CONFIG_FILE=/dev/null ... -m pip --isolated`, ignorando variáveis de ambiente do chamador (`PIP_TARGET`, `PIP_PREFIX`), `PIP_CONFIG_FILE` exportado e configurações globais/do usuário (`pip.conf`), garantindo instalação estrita no venv alvo. |
| **P-01** | Isolamento de configuração global/sistema do pip (`ConfigFileEnumerated != ConfigValueApplied`) | A invocação `PIP_CONFIG_FILE=/dev/null` + `pip --isolated` comprova que, mesmo quando arquivos globais (`/etc/pip.conf` ou via `XDG_CONFIG_DIRS`) definem `target = ...`, o pip em modo isolado não aplica essas configurações e instala estritamente no `site-packages` do venv (0 pacotes redirecionados). |
| **P-02** | Completude obrigatória da fronteira positiva de CLIs (16 CLIs mandatórias) | `REQUIRED_DISTRIBUTION_CLIS_V1` ancora as 16 ferramentas operacionais de CLI; manifestos do chamador omitindo qualquer CLI (ex.: `aiops-review-plan-chunks.py`, `aiops-review-build-payloads.py`, `github_agent_review.py`) falham closed na validação e na materialização. |
| **P-03** | Auditoria de imports diretos de scripts proibidos de runtime | `REQUIRED_FORBIDDEN_RUNTIME_SCRIPT_IMPORT_ROOTS_V1` audita imports diretos (`import migrate_savings_to_sqlite`, `from compare_aiops_runtimes import run`), impedindo que scripts presentes no monorepo via `scripts/` em `sys.path` passem na validação mas falhem na materialização. |
| **P-04** | Qualificação de ABI/largura de ponteiro de 64 bits (`struct.calcsize("P") * 8 == 64`) | O probe isolado valida a largura de ponteiro de 64 bits antes da criação do venv, impedindo que interpretadores CPython de 32 bits passem na checagem e falhem tardiamente na instalação de wheels compiled manylinux2014_x86_64. |
| **P-05** | Validação de tipos de caminhos declarados (diretórios vs arquivos regulares) | Valida que árvores em `core_packages` e `required_asset_trees` sejam estritamente diretórios (e pacotes contenham `__init__.py`), enquanto `package_roots`, `shared_primitives`, `install_boundary` e `distribution_clis` sejam arquivos regulares; impede que árvores substituídas por arquivos regulares ou arquivos substituídos por diretórios passem na validação. |
| **P-06** | Comparação de dependências externas exclusivamente no import root | Compara dependências proibidas apenas no componente raiz de importação (`company` em `company.fastapi.client`, `vendor` em `vendor.httpx`), evitando falsos positivos em submódulos namespaced, enquanto imports diretos ou via namespace `scripts` continuam sendo auditados fail-closed. |
| **P-07** | Rejeição de symlinks em ancestrais de caminhos declarados | A inspeção fail-closed de symlinks verifica cada componente do caminho entre a raiz do repositório e o item declarado (`curr = curr / part; curr.is_symlink()`), garantindo que caminhos alcançados através de diretórios-pai symlinkados (ex.: `templates -> undeclared_templates`) sejam rejeitados na validação estática e na materialização segura. |
| **P-08** | Correspondência exata da raiz de importação de pacotes internos (`app` e `scripts`) | O parser AST verifica a correspondência exata do componente raiz (`root_pkg == "app"` ou `root_pkg == "scripts"`), impedindo que pacotes externos legítimos cujos nomes apenas começam com `app` (ex.: `appdirs`, `application`) sejam erroneamente classificados como módulos internos de `app` e rejeitados. |
| **P-09** | Preservação de atestação de identidade de fonte imutável na materialização (`.source-commit` e `.toolrepo-sha`) | A materialização grava arquivos de atestação imutáveis com o SHA exato de 40 dígitos hexadecimais da fonte, permitindo que consumidores e instaladores (`install-agent-review-toolrepo.sh`) validem o pino `--toolrepo-sha` na ausência de metadados Git do repositório original. |
| **P-10** | Exclusão mútua mandatória entre `--check` e `--materialize-to` (preservação estrita de write-zero) | O CLI rejeita a combinação concorrente de `--check` e `--materialize-to`, garantindo fail-closed (código de saída 2) e assegurando que invocações de validação ou dry-run permaneçam estritamente write-zero (0 arquivos gravados no destino). |
| **P-11** | Recusa de materialização a partir de working tree suja (`git status --porcelain`) | Quando o repositório fonte contém arquivos modificados ou não rastreados dentro da fronteira declarada de distribuição, a materialização recusa fail-closed antes da criação do destino, impedindo atestar um commit para bytes modificados em disco. |
| **P-12** | Operabilidade nativa das CLIs de target-pack (`init` e `doctor`) em árvore standalone sem Git | Leitor de materiais com suporte a atestação (`.source-commit`/`.toolrepo-sha`) carrega templates e schemas diretamente de diretórios materializados, viabilizando execução integral do CLI sem necessidade de repositório Git sintético. |
| **P-13** | Rejeição de arquivos especiais (FIFO, socket, device) dentro das árvores declaradas | A validação (`--check`) e a materialização realizam varredura pre-creation em todos os membros e descendentes declarados, rejeitando fail-closed qualquer arquivo não-regular (FIFO, socket, device) antes de criar diretório de destino (write-zero). |
| **P-14** | Validação estrita de `--source-sha` contra a identidade independente da fonte | Sobrecarga explícita `--source-sha` exige correspondência exata contra a identidade resolvida independentemente (Git HEAD ou atestação prévia) e recusa valores all-zero (`0`*40) ou SHAs arbitrários, impedindo fabricação de prova. |
| **P-15** | Rejeição de arquivos ignorados pelo Git antes da materialização (`--others --ignored`) | A materialização valida a ausência de arquivos ignorados pelo Git (ex.: `*.key`, `.env`, `*.pem`) nas árvores declaradas antes de criar o diretório de destino (write-zero), filtrando exclusivamente bytecode Python (`__pycache__`/`*.pyc`). |
| **P-16** | Precedência mandatória do Git HEAD sobre arquivos de atestação locais | Em checkout Git, `git rev-parse HEAD` é a autoridade única e mandatória de identidade; arquivos `.source-commit` e `.toolrepo-sha` divergentes causam recusa fail-closed; atestações locais são autoridade apenas na ausência de repositório Git e exigem paridade recíproca. |
| **P-17** | Rejeição de arquivos especiais (FIFO) antes do parse AST com tempo delimitado | Arquivos especiais (como FIFOs) com terminação `.py` são rejeitados na admissão física preliminar e excluídos da auditoria AST, impedindo que chamadas `open()` travem a execução indefinidamente. |
| **P-18** | Fechamento positivo de dependências externas contra pacotes permitidos e stdlib | A validação estática projeta `allowed_third_party_packages` para import roots conhecidos e audita todas as dependências externas contra a biblioteca padrão e os pacotes permitidos, rejeitando fail-closed qualquer pacote externo não declarado. |
| **P-19** | Varredura recursiva de árvores de schemas com paridade Git vs Standalone | A enumeração de schemas opera recursivamente (`rglob("*.schema.json")` / `git ls-tree -r`) com ordenação determinística e chaves relativas, garantindo digests idênticos sob consumo Git e distribuição standalone para schemas em qualquer profundidade. |
| **P-20** | Confinamento estrito de templates e schemas standalone à raiz do toolrepo | Leituras de templates e enumeração de schemas na distribuição standalone utilizam a autoridade `validate_external_input_file_v2`, recusando fail-closed symlinks que escapem da raiz do toolrepo (`EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2`). |

---

## 7. Ledger de Claims Positivos

- **B0-S1 (Manifest Schema & Boundary):** O manifesto declara formalmente todas as fontes, assets, CLIs e contratos de instalação necessários para a operação do AgentReview.
- **B0-S2 (Negative Runtime Anchors):** As superfícies exclusivas do AIOps Runtime são expressamente proibidas e protegidas por âncoras mandatórias e checagem bidirecional de sobreposição.
- **B0-S3 (Direct-Import Linting):** A árvore de código é livre de imports diretos para pacotes e módulos de runtime proibidos.
- **B0-M1 (Pre-Creation Disjointness):** O diretório de destino é validado como disjunto das origens antes de qualquer criação ou escrita em disco.
- **B0-M2 (Clean Destination Confinement):** A materialização ocorre exclusivamente em diretórios ausentes ou vazios, rejeita destinos symlink e confina a escrita a `target_resolved`.
- **B0-E1 (Subprocess Origin Isolation):** A execução em subprocesso com `PYTHONPATH` isolado comprova que todas as importações resolvem estritamente dentro da raiz materializada.
- **B0-E2 (Negative Runtime Absence):** A tentativa de importar módulos de runtime a partir da materialização resulta em `ModuleNotFoundError` comprovadamente atribuído à ausência do módulo proibido ou de seu pacote ancestral, e não a falha transitiva de dependência em módulo vazado.
- **B0-E3 (Operational Coexistence):** As capacidades offline v1 e v2 funcionam comprovadamente no ambiente standalone.
- **B0-I1 (Install Boundary Authority):** A autoridade única das dependências do toolrepo reside em `requirements-agent-review.lock`, instalado em venv estritamente novo/ausente.
- **B0-I2 (Minimal Toolrepo Verification):** A suíte de instalação confirma a ausência de pacotes de runtime e a rejeição criptográfica de hashes alterados (preservando formato 64-hex).

---

## 8. Não-Alegações Explícitas (Non-Claims)

Em conformidade estrita com o método AOCM/CAEM e as decisões arquiteturais:

- **B0-N1 (Sem prova estática universal de imports dinâmicos):** Não se alega prova estática exaustiva contra todo padrão dinâmico de `importlib`, metaprogramação ou injeção de bytecode. A proteção contra desvios dinâmicos reside na camada executável (Layer E).
- **B0-N2 (Sem mapeamento universal AST -> PyPI):** Não se alega mapeamento estático universal entre identificadores de import AST e nomes de distribuições no PyPI. A autoridade de instalação de dependências reside exclusivamente no lockfile e na Layer I.
- **B0-N3 (Empacotamento padrão adiado):** A criação de artefatos padronizados de distribuição (`pyproject.toml`, wheels, sdist) está expressamente postergada para a fatia `#351-B1`.
- **B0-N4 (Sem prova de caminhos latentes não executados):** A garantia de funcionamento em isolamento é fornecida pelos testes focais representativos (Layer E), não por prova estática de cobertura total de fluxos latentes.
- **B0-N5 (Sem migração física ou desativação de runtime):** A separabilidade está provada; a migração física não foi executada. O AIOps Runtime, seus modelos, rotas e dependências continuam integralmente em operação no repositório.
