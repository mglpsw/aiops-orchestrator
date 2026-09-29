# AgentReview Standalone Distribution Closure (#351-B0)

**Status:** `VERIFIED | B0 DISTRIBUTION CLOSURE PROVEN`  
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

### Countermodels Focais

- **M1 — Omissão de primitiva compartilhada:** Remover `strict_json.py` causa falha causal direta (`ModuleNotFoundError`) nos módulos dependentes.
- **M2 — Omissão de assets de templates:** Remover `templates/agentreview-v2-target-pack` causa falha imediata e controlada em `build_target_pack_manifest_v2` com `TargetPackBuildError(BUILD_TEMPLATE_ROOT_MISSING_REASON_V2)`.
- **M3 — Escape para o checkout original:** O detector de escape rejeita qualquer resolução de módulo cujo `__file__` aponte para fora da raiz standalone.
- **M4 — Injeção de dependência de runtime:** Declarar `app/models/database.py` ou `fastapi` na distribuição é imediatamente rejeitado pelo validador.
- **M5 — Omissão de contrato de instalação:** A ausência de `requirements-agent-review.lock` invalida a distribuição.
- **M6 — Fronteira parcial v1 ou v2:** A exclusão acidental de arquivos de qualquer um dos perfis causa falha no controle positivo correspondente.

---

## 7. Não-Alegações Explícitas (B0-C8)

Em conformidade estrita com o método AOCM/CAEM:

1. **A separabilidade está provada; a migração não foi realizada.** Os arquivos continuam em seus caminhos originais no repositório `aiops-orchestrator`.
2. **O AIOps Runtime não foi aposentado.** Os endpoints, o banco de dados e os serviços do orquestrador continuam intactos.
3. **Nenhum target externo foi repinado.** `AgentEscala`, `caem` e outros consumidores mantêm seus pins inalterados.
4. **Nenhuma issue foi fechada.** As issues `#351`, `#19`, `#46` e `#358` permanecem abertas.
