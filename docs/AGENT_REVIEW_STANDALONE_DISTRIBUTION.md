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
|   - Destino limpo: ausente ou vazio, nunca symlink (T-05, R-02)         |
|   - Confinamento do destino relativo ou absoluto a target_resolved      |
|   - Cópia estrita dos membros declarados e fechamento de saída          |
+------------------------------------+------------------------------------+
                                     |
                                     v
+-------------------------------------------------------------------------+
| Layer E: Executable Isolation Evidence                                  |
|   - Subprocesso isolado com PYTHONPATH apontando APENAS ao standalone   |
|   - Origem dos módulos provada dentro do diretório standalone (M3)      |
|   - Módulos de runtime ausentes (ModuleNotFoundError comprovado)        |
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
|   - Script canônico de instalação para toolrepos consumidores           |
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
| **DISTRIBUTION_CLIS** | `scripts/aiops-review-*.py`<br>`scripts/agent-review-target-pack-v2.py`<br>`scripts/export-agent-review-v2-schemas.py`<br>`scripts/verify-agent-review-v2-conformance.py`<br>`scripts/github_agent_review.py` | Ferramental de linha de comando para automação de revisão, intake, parsing, target-pack e conformidade. |
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
O manifesto declara explicitamente a lista `forbidden_runtime_surfaces`, cuja presença e completude são obrigatórias (`REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1`). Nenhuma dessas superfícies pode ser incluída na distribuição nem importada por arquivos da mesma:

- `app/main.py`
- `app/api`
- `app/agent_router`
- `app/models`
- `app/policies`
- `app/adapters`
- `app/services/orchestrator.py`
- `deploy`
- `config/actions.yaml`

### Pacotes Proibidos de Runtime
O closure do AgentReview em modo standalone não requer nem importa pacotes pertencentes exclusivamente ao runtime:
- `fastapi`, `uvicorn`, `sqlalchemy`, `aiosqlite`, `asyncpg`, `psycopg`, `psycopg2`, `httpx`

### Política de Symlinks Fail-Closed
Nenhum arquivo ou diretório declarado na distribuição pode ser um link simbólico, nem conter symlinks internos (`any sub.is_symlink() -> FAIL CLOSED`). Qualquer link simbólico detectado na validação ou na materialização causa rejeição imediata, eliminando deterministicamente riscos de ciclos, travessia de subárvores ou escapes do repositório. Da mesma forma, o diretório de destino da materialização não pode ser um symlink.

---

## 5. Validação e Materialização Determinística

O validador [`scripts/verify-agent-review-standalone-closure.py`](../scripts/verify-agent-review-standalone-closure.py) implementa as camadas estática e de materialização (Layers S e M):

1. **Validação de Schema e Âncoras (Layer S):** Valida `manifest_version`, seções obrigatórias e âncoras positivas/negativas (`REQUIRED_BOUNDARY_ANCHORS_V1` e `REQUIRED_FORBIDDEN_RUNTIME_SURFACES_V1`).
2. **Verificação de Caminhos e Symlinks (Layer S):** Assegura que todos os caminhos sejam canônicos, existam em disco, permaneçam confinados ao repositório e não contenham symlinks.
3. **Inspeção de AST (Layer S):** Analisa recursivamente todas as árvores de código Python da distribuição declarada e garante que nenhum import direto de módulo ou pacote proibido exista.
4. **Materialização Segura (Layer M):** A função `materialize_standalone_distribution(repo_root, target_dir)` valida o manifesto, verifica disjunção de origem/destino, assegura que o destino seja diretório vazio ou ausente (nunca symlink), confina a escrita estritamente a `target_resolved` e verifica o fechamento de saída.

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

---

## 7. Ledger de Claims Positivos

- **B0-S1 (Manifest Schema & Boundary):** O manifesto declara formalmente todas as fontes, assets, CLIs e contratos de instalação necessários para a operação do AgentReview.
- **B0-S2 (Negative Runtime Anchors):** As superfícies exclusivas do AIOps Runtime são expressamente proibidas e protegidas por âncoras mandatórias e checagem bidirecional de sobreposição.
- **B0-S3 (Direct-Import Linting):** A árvore de código é livre de imports diretos para pacotes e módulos de runtime proibidos.
- **B0-M1 (Pre-Creation Disjointness):** O diretório de destino é validado como disjunto das origens antes de qualquer criação ou escrita em disco.
- **B0-M2 (Clean Destination Confinement):** A materialização ocorre exclusivamente em diretórios ausentes ou vazios, rejeita destinos symlink e confina a escrita a `target_resolved`.
- **B0-E1 (Subprocess Origin Isolation):** A execução em subprocesso com `PYTHONPATH` isolado comprova que todas as importações resolvem estritamente dentro da raiz materializada.
- **B0-E2 (Negative Runtime Absence):** A tentativa de importar módulos de runtime a partir da materialização resulta em `ModuleNotFoundError`.
- **B0-E3 (Operational Coexistence):** As capacidades offline v1 e v2 funcionam comprovadamente no ambiente standalone.
- **B0-I1 (Install Boundary Authority):** A autoridade única das dependências do toolrepo reside em `requirements-agent-review.lock`.
- **B0-I2 (Minimal Toolrepo Verification):** A suíte de instalação confirma a ausência de pacotes de runtime e a exatidão dos hashes fixados.

---

## 8. Não-Alegações Explícitas (Non-Claims)

Em conformidade estrita com o método AOCM/CAEM e as decisões arquiteturais:

- **B0-N1 (Sem prova estática universal de imports dinâmicos):** Não se alega prova estática exaustiva contra todo padrão dinâmico de `importlib`, metaprogramação ou injeção de bytecode. A proteção contra desvios dinâmicos reside na camada executável (Layer E).
- **B0-N2 (Sem mapeamento universal AST -> PyPI):** Não se alega mapeamento estático universal entre identificadores de import AST e nomes de distribuições no PyPI. A autoridade de instalação de dependências reside exclusivamente no lockfile e na Layer I.
- **B0-N3 (Empacotamento padrão adiado):** A criação de artefatos padronizados de distribuição (`pyproject.toml`, wheels, sdist) está expressamente postergada para a fatia `#351-B1`.
- **B0-N4 (Sem prova de caminhos latentes não executados):** A garantia de funcionamento em isolamento é fornecida pelos testes focais representativos (Layer E), não por prova estática de cobertura total de fluxos latentes.
- **B0-N5 (Sem migração física ou desativação de runtime):** A separabilidade está provada; a migração física não foi executada. O AIOps Runtime, seus modelos, rotas e dependências continuam integralmente em operação no repositório.
