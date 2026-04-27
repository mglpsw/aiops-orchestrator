# Testes — AIOps Orchestrator

## Comando canônico

```bash
bash scripts/test.sh
```

Funciona dentro do CT 102, sem Docker, Prometheus, Ollama ou secrets reais.
É o mesmo comando usado pelo CI (GitHub Actions).

---

## Requisitos

| Componente | Versão mínima |
|---|---|
| Python | 3.11 |
| pip packages | `requirements.txt` |

### Instalar dependências

```bash
pip install -r requirements.txt
```

Não é necessário criar venv se já estiver em ambiente isolado (CT/container).
Para desenvolvimento local fora do CT:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Tipos de testes

### Unit (padrão, offline)

Não exigem Docker, rede, Prometheus, Ollama ou secrets reais.
Variáveis de ambiente são configuradas com valores fake nos fixtures.

```bash
bash scripts/test.sh
# ou
python3 -m pytest -q
```

### Integration (opt-in)

Exigem serviços externos ativos (Prometheus em `192.168.3.200:9090`, etc.).

```bash
AIOPS_INTEGRATION=1 bash scripts/test.sh
```

### Filtros por nome ou módulo

```bash
bash scripts/test.sh -k test_action_catalog
bash scripts/test.sh tests/test_policy_engine.py
bash scripts/test.sh --co   # só collect, sem executar
```

---

## Variáveis de ambiente para testes

Os testes unitários **não precisam** de variáveis reais. Valores fake são
injetados via `conftest.py` ou defaults seguros nas configurações.

Se um teste específico precisar de variável real, ele deve ser marcado com
`@pytest.mark.integration` e excluído do CI padrão.

### `.env.example` como referência

O arquivo `.env.example` documenta todas as variáveis suportadas com valores
padrão seguros. Nunca copie secrets reais para `.env.example`.

---

## CI (GitHub Actions)

O workflow em [.github/workflows/ci.yml](../.github/workflows/ci.yml)
executa exatamente:

```bash
bash scripts/test.sh
```

Para reproduzir o CI localmente:

```bash
pip install -r requirements.txt
bash scripts/test.sh
```

---

## Verificações de segurança (além dos testes)

```bash
# Sintaxe de todos os scripts
bash -n scripts/*.sh

# Catálogo de actions (sem Docker)
bash scripts/validate_actions_catalog.sh

# Compose config (sem iniciar nada)
docker compose -p aiops-orchestrator -f deploy/docker-compose.yml config

# Validação local completa (read-only, dentro do CT 102)
bash scripts/validate.sh
```

---

## Marcadores pytest

| Marcador | Significado |
|---|---|
| `integration` | Requer serviços externos |
| `requires_docker` | Requer Docker acessível |
| `requires_prometheus` | Requer Prometheus em `PROMETHEUS_URL` |
| `requires_network` | Requer acesso a rede externa |

Para adicionar a um teste:

```python
@pytest.mark.integration
@pytest.mark.requires_prometheus
def test_prometheus_query_live():
    ...
```

---

## Troubleshooting

### `ModuleNotFoundError`

```bash
pip install -r requirements.txt
```

### `AIOPS_*` env var ausente

Os testes unitários não precisam de variáveis reais. Se um teste falhar por
variável ausente, ele deveria ser marcado como `integration`.

### Porta 8000 ocupada durante testes

Os testes usam `TestClient` do FastAPI (in-process), não iniciam servidor
real. Porta 8000 não é necessária.

### Testes de guardrail falhando

Verifique se `app/policies/command_guardrails.py` e `app/policies/engine.py`
estão atualizados com os padrões esperados pelos testes.
