# AgentReview Standalone Distribution Closure (#351-B0)

**Status:** `CANDIDATE | B0 DISTRIBUTION CLOSURE — exact-head qualification pending`
**Owner:** `#351` (distribution & source separation track)  
**Roadmap:** `#46` (canonical roadmap authority)  
**Reference:** [`docs/CROSS_REPO_DISSOLUTION_MATRIX.md`](CROSS_REPO_DISSOLUTION_MATRIX.md)  

---

## 1. Objetivo e Proposição Central

Esta especificação e seu ferramental associado materializam a menor fatia executável de **#351-B — distribuição independente**, provando formal e deterministicamente a seguinte proposição:

> **AgentReview pode ser materializado, instalado, importado e exercitado em modo offline a partir de uma fronteira explícita de produto, sem depender do código-fonte ou das dependências exclusivas do AIOps Runtime.**

Esta fatia **não executa a separação física nem aposenta o AIOps Runtime**. Trata-se de uma **prova de separabilidade** (`SeparabilityProven != PhysicalMigrationExecuted`).

---

## 2. Fronteira Declarada do Produto

A fronteira do produto é formalizada e versionada na projeção canônica:  
[`config/agent-review/standalone-distribution-manifest.v1.json`](../../config/agent-review/standalone-distribution-manifest.v1.json).

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

---

## 3. Invariantes de Dependências Compartilhadas

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

## 4. Fronteira Negativa de Dependências Externas

O closure do AgentReview em modo standalone não requer nem importa pacotes pertencentes exclusivamente ao runtime:

- `fastapi`
- `uvicorn`
- `sqlalchemy`
- `aiosqlite`
- `asyncpg`
- `psycopg` / `psycopg2`
- `httpx`

A closure de pacotes permitidos é estritamente: `pydantic` (e dependências diretas de tipagem) e `pyyaml`, conforme fixado em `requirements-agent-review.lock`.

---

## 5. Validação e Materialização Determinística

O validador [`scripts/verify-agent-review-standalone-closure.py`](../../scripts/verify-agent-review-standalone-closure.py) fornece:

1. **Inspeção de AST em tempo real:** Analisa recursivamente todas as árvores de código Python da distribuição declarada e garante que nenhum import proibido exista.
2. **Verificação de integridade da fronteira:** Assegura que todos os arquivos declarados existam e que nenhuma superfície de runtime tenha sido incluída.
3. **Materialização em diretório temporário:** A função `materialize_standalone_distribution(repo_root, target_dir)` copia estritamente os arquivos da fronteira, garantindo que o diretório resultante seja isolado e funcional.

Execução da verificação:

```bash
python3 scripts/verify-agent-review-standalone-closure.py --check
```

---

## 6. Provas Automatizadas e Matriz de Countermodels

A suíte [`tests/agent_review/test_standalone_distribution_closure.py`](../../tests/agent_review/test_standalone_distribution_closure.py) implementa:

### Controles Positivos

1. **Validação do Manifest:** O arquivo de configuração atende a todas as restrições da especificação.
2. **Closure de AST:** Zero violações em mais de 80 módulos e scripts do AgentReview.
3. **Execução em Subprocesso Isolado:** A materialização standalone é executada com `PYTHONPATH` apontando exclusivamente para a raiz temporária. Nenhum módulo do AIOps Runtime consegue ser importado (`ModuleNotFoundError`).
4. **Controle Positivo v1:** Classificação determinística de arquivos e leitura de veredictos sem provedores externos.
5. **Controle Positivo v2:** Execução de `build_target_pack_manifest_v2` navegando pela árvore git dos `templates/` e `schemas/` dentro da raiz temporária standalone, gerando manifest e hashes válidos.
6. **Coexistência v1/v2:** Ambas as capacidades são exercitadas no mesmo ambiente materializado sem interferência mútua.

### Countermodels Focais e Controles de Fronteira

- **M1 — Omissão de primitiva compartilhada:** Remover `strict_json.py` causa falha causal direta (`ModuleNotFoundError`) nos módulos dependentes.
- **M2 — Omissão de assets de templates:** Remover `templates/agentreview-v2-target-pack` causa falha imediata e controlada em `build_target_pack_manifest_v2` com `TargetPackBuildError(BUILD_TEMPLATE_ROOT_MISSING_REASON_V2)`.
- **M3 — Escape para o checkout original:** O detector de escape rejeita qualquer resolução de módulo cujo `__file__` aponte para fora da raiz standalone.
- **M4 — Injeção de dependência ou módulo de runtime:** Declarar uma superfície proibida de runtime (`app/models/database.py`) ou injetar código com importação de pacote proibido (`import fastapi`) em arquivos da distribuição é detectado e rejeitado deterministicamente pelo validador via inspeção de AST.
- **M5 — Violação do contrato estrutural de instalação:** Omitir artefatos obrigatórios da fronteira de instalação (`requirements-agent-review.lock`, `scripts/install-agent-review-toolrepo.sh`, `docs/AGENT_REVIEW_V2_INSTALLATION.md`), declarar fronteira vazia (`install_boundary: []`), apontar arquivo inexistente ou usar schema/versão não suportada invalida deterministicamente a distribuição.
- **M6 — Fronteira parcial v1 ou v2:** A exclusão acidental de arquivos de qualquer um dos perfis causa falha no controle positivo correspondente.
- **T-01 — Não-vacuidade estrutural das seções centrais:** O manifesto v1 não pode omitir ou esvaziar seções estruturais obrigatórias (`core_packages`, `package_roots`, `shared_primitives`, `required_asset_trees`, `install_boundary`, `distribution_clis`) nem omitir âncoras essenciais do produto, diferenciando omissão de contrato de arquivo inexistente em disco.
- **T-02 — Confinamento de `ImportFrom` e star imports:** Submódulos e pacotes proibidos de runtime não escapam via módulos-pai permitidos (ex.: `from app import models` e `from app.services import orchestrator` são resolvidos e bloqueados deterministicamente, enquanto imports de símbolos permitidos não geram falsos positivos); imports internos ambíguos de asterisco (`from app import *`) falham closed.
- **T-03 — Confinamento de caminhos e symlinks:** Todos os caminhos declarados devem ser caminhos canônicos POSIX relativos ao repositório, rejeitando traversal (`..`), caminhos absolutos, separadores backslash ou espaços. A resolução em disco e a materialização comprovam que caminhos de origem e destino, incluindo symlinks, permanecem estritamente confinados às suas respectivas raízes.
- **T-04 — Rejeição de manifesto explícito vazio ou inválido:** Passagem explícita de `manifest={}` ou manifesto com esquema inválido é validada diretamente e rejeitada (`StandaloneClosureValidationError`), nunca realizando fallback silencioso para a configuração padrão do repositório (`load_manifest() if manifest is None else manifest`).
- **T-05 — Alvo de materialização limpo e fechamento de saída:** A materialização standalone exige que o diretório de destino seja ausente ou vazio; diretórios não-vazios falham closed sem mutação ou exclusão de arquivos preexistentes do chamador. Todos os arquivos no diretório final são verificados contra a fronteira copiada (fechamento de saída).
- **U-01 — Resolução de nós `ImportFrom` relativos:** Nós de importação relativa (`from .. import models`, `from . import contracts_v2`) são resolvidos contra a hierarquia de pacotes do arquivo importador antes de aplicar as checagens de módulos proibidos e de fronteira; imports que ultrapassam a raiz do pacote falham closed (`ValueError`).
- **U-02 — Imposição de fronteira exata de componente de pacote:** O prefixo permitido para o núcleo do AgentReview exige correspondência exata de componente (`mod == "app.agent_review" or mod.startswith("app.agent_review.")`), impedindo que pacotes irmãos (ex.: `app.agent_review_runtime`) passem na validação.
- **U-03 — Confinamento de árvores de diretório e bloqueio de symlinks internos:** Itens e symlinks contidos em diretórios declarados devem permanecer confinados à sua subárvore fonte declarada e não podem resolver para superfícies proibidas ou não declaradas do repositório (ex.: links para `../models`), falhando tanto na validação estática quanto na materialização.
- **U-04 — Disjunção estrita do destino de materialização:** O diretório de destino é inspecionado antes de qualquer criação ou escrita no disco, garantindo que seja estritamente disjunto de todos os caminhos fonte declarados (rejeitando destinos aninhados como `<repo>/app/agent_review/nested`, destinos idênticos a fontes e destinos que sejam ancestrais de fontes), prevenindo recursão infinita e mutação acidental.
- **U-05 — Sobreposição bidirecional de superfícies proibidas:** A detecção de superfícies proibidas de runtime aplica checagem bidirecional de componentes de caminho (`paths_overlap`), garantindo que tanto declarações de caminhos ancestrais (ex.: declarar `"config"`, que conteria `"config/actions.yaml"`) quanto descendentes de superfícies proibidas sejam bloqueadas deterministicamente.

---

## 7. Não-Alegações Explícitas (B0-C8)

Em conformidade estrita com o método AOCM/CAEM:

1. **A separabilidade está provada; a migração não foi realizada.** Os arquivos continuam em seus caminhos originais no repositório `aiops-orchestrator`.
2. **O AIOps Runtime não foi aposentado.** Os endpoints, o banco de dados e os serviços do orquestrador continuam intactos.
3. **Nenhum target externo foi repinado.** `AgentEscala`, `caem` e outros consumidores mantêm seus pins inalterados.
4. **Nenhuma issue foi fechada.** As issues `#351`, `#19`, `#46` e `#358` permanecem abertas.
