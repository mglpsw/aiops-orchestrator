# Testes — AIOps Orchestrator

## Contrato de scripts

| Script | Onde roda | O que valida |
|---|---|---|
| `scripts/test.sh` | Em qualquer lugar | Testes Python unitários (offline) |
| `scripts/ci_validate.sh` | GitHub Actions / agents | Explicit static/generated/pytest modes |
| `scripts/local_validate.sh` | Local / periodic GitHub | Full offline P + S + N, static/generated, receipt |
| `scripts/validate.sh` | Dentro do CT 102 | Runtime: container, project name, health, endpoints |

---

## Instalação de dependências

```bash
pip install -r requirements-dev.txt
```

`requirements-dev.txt` inclui `requirements.txt` + `pytest` + `pytest-xdist` (development only).

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

## Fast CI and full offline regression

Required `aiops-ci` has disjoint primary owners:

| Job | Scope |
|---|---|
| Validate repository | shell syntax, catalog/guardrails, compose config, git whitespace/structure, focused runner and validator tests |
| AgentReview release gates | generated schemas/evals, CAEM pin, RI/target-pack view, deterministic benchmark checks and safety |

Full ordinary pytest is not repeated in these jobs. Fast CI is not full
regression, and local validation is not GitHub required CI. The new workflow
changes its own test surface; a green run does not confer independent trust.

```bash
bash scripts/ci_validate.sh --repository       # first job, no pytest
bash scripts/ci_validate.sh --generated        # second job, no pytest
bash scripts/ci_validate.sh --static           # both static classes, once
bash scripts/ci_validate.sh --unit             # ordinary P + S
bash scripts/ci_validate.sh --requires-network # serial N
bash scripts/ci_validate.sh --all              # full offline (default)
```

Static validation requires Docker Compose config capability but never starts
containers or needs the daemon. Compose files are rendered in a temporary
copy with example environment values, preserving the checkout's `.env`.
The dangerous-pattern inventory is informational, as before; actual blocking
checks are the catalog validator and focused command guardrail tests.

`full-regression.yml` runs manually or weekly on master; all offline tests run
once through the canonical full runner. It is not required for each PR. Its
40-minute timeout accommodates regression work; required fast jobs have a
7-minute margin and should normally complete in 3–5 minutes including setup.
Inspect failures/cancellations in Actions and the retained artifact logs and
receipt; failure must trigger investigation. No issue-writing automation is
introduced. GitHub's job-timeout cancellation is incomplete evidence, not a
demonstrated test failure. A cancelled job may not finish uploading artifacts.

## Host capacity and explicit pytest lanes

`pytest-xdist` is a development dependency only. `scripts/test.sh` installs
nothing; it falls back explicitly to serial when xdist is absent.

```bash
python3 scripts/test_workers.py --workers
python3 scripts/test_workers.py --json
python3 scripts/test_workers.py --doctor
bash scripts/test.sh                         # parallel P, then serial S
bash scripts/test.sh --serial                # all ordinary tests serially
AIOPS_TEST_WORKERS=8 bash scripts/test.sh     # explicit operator override
bash scripts/test.sh --lane network --serial # N, no external runtime lanes
```

The stdlib selector observes visible CPUs, cgroup v2 CPU quota (v1 fallback),
effective cpuset, host MemAvailable, and cgroup memory maximum/current. It
checks conventional membership paths and ancestor limits. CPU capacity is the
minimum of visible CPU, floored quota (minimum one), and cpuset count. Memory
capacity uses the smaller of host availability and cgroup spare RAM. Unknown
memory is conservatively one worker. Automatic workers are the minimum of CPU,
memory capacity and `AIOPS_TEST_AUTO_MAX` (default 4). Reserve 2 GiB for the host
and controller; budget 1 GiB per Python worker because fixtures/subprocesses
are materially heavier than isolated pure functions. These are conservative
starting budgets, not resource enforcement or a performance guarantee.

`AIOPS_TEST_WORKERS=N` explicitly overrides automatic bounds; operators own its
capacity risk. Invalid or empty overrides fail. The hosted full regression
caps automatic workers at 2. `--doctor` only emits operational capacity fields,
never environment variables, credentials, provider configuration or payloads.
Nonstandard cgroup mount layouts may be unavailable; null fields expose that
limitation. Worker selection does not install software or change host limits.

| Lane | Classification | Execution |
|---|---|---|
| P | ordinary minus `serial_required` | xdist `-n N --dist=loadfile`, or explicit fallback |
| S | host namespace/carrier and parent-child process observation exceptions | serial |
| N | `requires_network`, excluding external runtime markers | serial |
| R | integration/runtime/docker/prometheus | outside offline validation |

`tests/conftest.py` assigns S using the explicit file list in
`scripts/test_lanes.py`. N includes real Git, process supervision, kill-group
and isolation tests, so it stays serial pending separate qualification. Per
process cwd/environment/monkeypatch state and private `tmp_path` fixtures alone
do not require S. P, S, N run sequentially; there is one worker budget, with no
concurrent suites each consuming that whole budget. New shared resources must
be characterized before moving their tests into P. Collection-only default
ordinary includes both P and S; zero selected tests report `SkippedByScope`.
Marker/worker overrides through pytest flags or PYTEST_ADDOPTS are rejected;
use the runner's explicit lanes and worker variables instead.

The existing `AIOPS_INTEGRATION=1` opt-in remains a serial legacy invocation;
external services/environment authority are the operator's responsibility.
It is never invoked by these workflows or offline full validation.

## Local full validation and receipts

```bash
bash scripts/local_validate.sh
bash scripts/local_validate.sh --receipt /tmp/aiops-receipt.json
```

This runs A: static/generated, B: P, C: S, D: N. Reports/logs and the default
receipt live in a temporary directory (RUNNER_TEMP on GitHub), never committed
automatically. The receipt binds repository, merge-base with origin/master,
HEAD/tree, dirty state/diff digest, Python, selected workers/capacity, exact
commands, start/end, lane exit statuses, counts and collection digests.
A dirty worktree is `NOT_QUALIFIED_DIRTY_WORKTREE`; movement during execution is
`SUBJECT_MOVED`; a per-lane timeout is `INCOMPLETE_TIMEOUT`. Missing/interrupted
reports do not manufacture zero-failure test counts. `--lane-timeout` defaults
to 1,800 seconds. Filters via PYTEST_ADDOPTS and integration opt-in are rejected
by canonical full validation to avoid narrowed coverage being called full.

Before Ready: focused tests; exact-HEAD full validation when risk requires;
required fast CI; Ready; Ready-triggered Codex; terminal review; TOCTOU; human
merge grant. Every later push stales a local receipt by default. Receipts do
not become required checks or automatic merge authority; no v2 attestation,
trust broker or readiness dependency is introduced.

The master base lacks `tests/test_post_ready_codex_guard.py` (owned by #370).
Fast CI runs it when present and explicitly reports `SkippedByScope` otherwise;
its requested remote qualification remains pending while absent.

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
| `requires_network` | Real Git/subprocess tests by repository convention; kept serial |
| `serial_required` | Host/process observation exception |

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
