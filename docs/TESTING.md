# Testes — AIOps Orchestrator

## Contrato de scripts

| Script | Onde roda | O que valida |
|---|---|---|
| `scripts/test.sh` | Em qualquer lugar | Testes Python unitários (offline) |
| `scripts/ci_validate.sh` | GitHub Actions / agents | Repo: scripts, catalog, compose syntax, testes |
| `scripts/validate.sh` | Dentro do CT 102 | Runtime: container, project name, health, endpoints |

---

## Instalação de dependências

```bash
pip install -r requirements-dev.txt
```

`requirements-dev.txt` inclui `requirements.txt` + `pytest`.

Para desenvolvimento local fora do CT:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

---

## Rodar testes

### Comando canônico (offline, funciona em qualquer lugar)

```bash
bash scripts/test.sh
```

Não exige Docker, Prometheus, Ollama, secrets reais ou CT 102.

### Testes de integração (opt-in)

```bash
AIOPS_INTEGRATION=1 bash scripts/test.sh
```

Requer serviços externos ativos.

### Filtros

```bash
bash scripts/test.sh -k test_action_catalog
bash scripts/test.sh tests/test_policy_engine.py
bash scripts/test.sh --co   # só collect, sem executar
```

---

## CI (GitHub Actions)

O workflow instala `requirements-dev.txt` e executa:

```bash
bash scripts/test.sh
```

Para reproduzir o CI localmente exatamente:

```bash
pip install -r requirements-dev.txt
bash scripts/test.sh
```

---

## Validação de repositório (CI-safe)

Roda bash syntax check, catálogo de actions, compose config e testes:

```bash
bash scripts/ci_validate.sh
```

## AgentReview v0.20.0

Os testes AgentReview são offline e não devem chamar CT102, providers, Agent
Router, Docker, SSH, deploy ou GitHub write APIs.

Suíte focada:

```bash
python3 -m pytest tests/agent_review -q
```

Contrato E2E:

```bash
python3 -m pytest \
  tests/agent_review/test_agent_review_e2e_contract.py -q
```

Esses testes cobrem, entre outros pontos:

- determinismo byte a byte;
- schemas e envelopes de artifacts;
- sanitização e redaction;
- outputs fora do target repository;
- imutabilidade das fixtures de origem e destino;
- `chunk_id` compatível com artifact/response file;
- preservação de evidência global, path-scoped e unscoped;
- quality gate fail-closed;
- sugestões de contrato `manual_only` e `applied: false`;
- rejeição do pipeline em ambiente production/runtime.

As CLIs manuais exigem o boundary explícito:

```bash
export AIOPS_ENVIRONMENT=dev
export AIOPS_NODE_ROLE=toolrepo
export AIOPS_REPO_MODE=agent_review_tooling
export AIOPS_PRODUCTION_RUNTIME=false
```

Não use essas variáveis no CT102.

## Validação documental

Antes de publicar mudanças somente de documentação:

```bash
git diff --check
python3 -m pytest tests/agent_review/test_docs_agentescala_contract.py -q
```

Também verifique que todos os links Markdown relativos apontam para arquivos
existentes e que o diff permanece limitado a `README.md`, `CHANGELOG.md` e
`docs/`.

---

## Validação de runtime (somente CT 102)

Verifica container em produção, project name, health/ready:

```bash
bash scripts/validate.sh
```

Este script **não roda no GitHub Actions** — depende do CT 102 em execução.

---

## Marcadores pytest

Registrados em `pytest.ini`:

| Marcador | Significado |
|---|---|
| `integration` | Requer serviços externos |
| `requires_runtime` | Requer runtime em produção (CT 102) |
| `requires_docker` | Requer Docker daemon acessível |
| `requires_prometheus` | Requer Prometheus em `PROMETHEUS_URL` |
| `requires_network` | Requer acesso à rede externa |

Uso:

```python
@pytest.mark.integration
@pytest.mark.requires_prometheus
def test_prometheus_query_live():
    ...
```

---

## Troubleshooting

### `No module named pytest`

```bash
pip install -r requirements-dev.txt
```

### `ModuleNotFoundError` para outros módulos

```bash
pip install -r requirements-dev.txt
```

### `AIOPS_*` env var ausente em testes

Os testes unitários não precisam de variáveis reais. Se um teste falhar por
variável ausente, marque com `@pytest.mark.requires_runtime`.

### Porta 8000 ocupada durante testes

Os testes usam `TestClient` do FastAPI (in-process), não abrem a porta 8000.

### PytestUnknownMarkWarning

Se aparecer warning sobre markers desconhecidos, verifique se `pytest.ini`
contém o marker em questão na seção `markers`.

### Testes de guardrail falhando

Verifique `app/policies/command_guardrails.py` e `app/policies/engine.py`.
