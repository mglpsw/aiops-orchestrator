# AIOps Diagnostic Engine v1 Plan

## Objetivo
AIOps v1 sera somente diagnostico, sem execucao real.

O objetivo desta fase e transformar o estado atual do repo em uma superficie segura de observacao e analise, preservando o que ja e util, isolando o que ainda executa comandos, e deixando qualquer remediacao para uma fase futura separada, allowlisted e com approval.

## Escopo incluido no v1

- Coletar sinais internos.
- Usar readiness atual.
- Usar metricas allowlisted.
- Gerar findings.
- Calcular severity.
- Sugerir recommended_actions em dry-run.
- Nunca executar comandos.

## Fora do escopo no v1

- Shell real.
- SSH real.
- Docker exec real.
- Remediacao automatica.
- Comando livre vindo de LLM.
- Alteracao de producao.
- Deploy/restart/delete.

## Componentes a manter

### Mantem no caminho do v1

- `app/main.py`: ja expoe `GET /health`, `GET /ready` e `GET /metrics` de forma simples e previsivel.
- `app/api/auth.py`: fornece o gate de autenticacao atual e pode ser reutilizado para proteger o endpoint de diagnostico.
- `app/api/metrics.py`: e a base de telemetria atual e pode alimentar sinais agregados do v1.
- `app/models/database.py`: ja sustenta persistencia e auditoria; e util para registrar diagnósticos sem acoplar execucao.
- `app/models/schemas.py`: e o lugar natural para adicionar os schemas do v1 sem quebrar o contrato atual.
- `app/services/task_service.py`: ja encapsula persistencia, auditoria e metricas; pode ser reaproveitado para observabilidade do diagnostico.
- `app/policies/engine.py`: a logica de seguranca, denylist e allowlist e util como base para classificar o que e diagnostico vs. o que e acao.
- `app/adapters/ollama.py`: util como fonte de LLM local para classificacao e sumarizacao de sinais.
- `app/adapters/claude.py`: util para analise e correlações de maior qualidade quando configurado.
- `app/adapters/openai_compatible.py`: util como fallback generico para providers compatíveis.
- `app/utils/logging.py`: necessario para trilha de auditoria e analise operacional.
- `app/utils/secrets.py`: necessario para nao vazar segredos nos logs e saidas.
- `tests/test_api_middleware.py`: prova a barreira de auth e CORS basicos.
- `tests/test_policy_engine.py`: prova que o denylist de alto risco esta funcionando e deve ser preservado.

### Porque estes ficam

Estes componentes ja entregam infraestrutura segura, persistencia, auth e telemetria. O v1 nao precisa substituir isso, apenas parar de usar a parte de execucao real como caminho principal.

## Componentes a isolar

### Isolar do v1 diagnostico

- `app/services/orchestrator.py`
- `app/adapters/executor_local.py`
- `app/adapters/executor_ssh.py`
- `app/adapters/docker.py`
- `app/adapters/codex.py`
- `app/api/routes.py` como superficie operacional existente

### Risco de cada um

- `app/services/orchestrator.py`: hoje ele mistura classificacao, planejamento, approval gate e execucao real. Esse acoplamento e o principal ponto de risco para o v1.
- `app/adapters/executor_local.py`: usa `asyncio.create_subprocess_shell`, entao qualquer erro de validacao vira shell real no host.
- `app/adapters/executor_ssh.py`: executa `ssh` via shell e amplia o raio de impacto para hosts remotos.
- `app/adapters/docker.py`: executa `docker` via shell, o que ainda e alteracao operacional real, mesmo que pareca "somente container".
- `app/adapters/codex.py`: empurra o sistema para code/infra automation, que nao e o objetivo do v1 diagnostico.
- `app/api/routes.py`: concentra endpoints que podem disparar aprovacao e execucao; isso precisa ficar fora da superficie do v1.

### Como isolar

- O v1 deve ler sinais, nao enviar comandos.
- O v1 deve falar com a camada de observabilidade, nao com executores.
- O v1 deve produzir output estruturado de diagnostico, nao plans executaveis.
- Qualquer executor real deve viver em outro caminho e em outro contrato.

## Componentes a refatorar

### Separacao de responsabilidades

1. Diagnostico
2. Planejamento
3. Execucao

Hoje esses tres passos estao acoplados no mesmo fluxo. Para o v1, o diagnostico deve ser uma linha independente:

- entrada: sinais, readiness, metricas allowlisted e contexto minimo
- processamento: correlacao, severidade e recomendacao
- saida: findings, severity e recommended_actions

### Como refatorar sem quebrar o legado

- Manter o fluxo atual intocado ate o v1 novo estar pronto.
- Introduzir uma camada de diagnostico nova em vez de reaproveitar o caminho de execucao.
- Reusar auth, logging, persistence e metrics.
- Nao reutilizar o trecho que cria `command` e chama executores.
- Nao permitir que o esquema do v1 herde `steps` executaveis.

### Ponto tecnico principal

O corte mais importante e remover a dependencia de `command` livre no diagnostico. O v1 deve olhar estado e apresentar conclusoes, nao produzir instrucoes de shell.

## Modelo de dados proposto

### `AIOpsDiagnoseRequest`

Campos sugeridos:

- `target`: string
- `scope`: string
- `checks`: list[string]
- `dry_run`: bool

### `AIOpsDiagnoseResponse`

Campos sugeridos:

- `status`: `ok|warning|critical|unknown`
- `severity`: `low|medium|high`
- `summary`: string
- `signals`: list[`AIOpsSignal`]
- `findings`: list[`AIOpsFinding`]
- `recommended_actions`: list[`AIOpsRecommendedAction`]
- `dry_run`: bool

### `AIOpsSignal`

Representa um sinal bruto ou semi-estruturado coletado do sistema.

Campos sugeridos:

- `name`
- `source`
- `value`
- `unit`
- `timestamp`
- `status`
- `details`

### `AIOpsFinding`

Representa uma conclusao do motor de diagnostico.

Campos sugeridos:

- `id`
- `title`
- `description`
- `severity`
- `evidence`
- `affected_area`
- `confidence`

### `AIOpsRecommendedAction`

Representa uma recomendacao textual, sem execucao.

Campos sugeridos:

- `title`
- `description`
- `priority`
- `dry_run_only`
- `next_step_hint`

## Endpoint proposto

`POST /v1/aiops/diagnose`

### Request esperado

```json
{
  "target": "agent-router",
  "scope": "self",
  "checks": ["readiness", "error_rate", "latency", "backend_up"],
  "dry_run": true
}
```

### Response esperado

```json
{
  "status": "ok|warning|critical|unknown",
  "severity": "low|medium|high",
  "summary": "...",
  "signals": [],
  "findings": [],
  "recommended_actions": [],
  "dry_run": true
}
```

### Observacoes de desenho

- O endpoint deve ser diagnostico-only.
- O endpoint nao deve aceitar comando livre.
- O endpoint nao deve chamar executor real.
- O endpoint deve ser seguro para uso em verificacoes automatizadas.

## Checks v1

Checks iniciais propostos:

- `readiness`
- `backend_up`
- `error_rate`
- `latency_p95`
- `blocked_tasks`
- `model_selection`
- `ollama_models_count`

## Politica de severidade

Regras simples para o v1:

- `critical` se `readiness` estiver `not_ready` ou backend obrigatorio estiver down.
- `warning` se houver degraded state, latencia alta ou erro elevado.
- `ok` se os sinais estiverem normais.
- `unknown` se metricas ou sinais nao estiverem disponiveis.

### Prioridade da regra

1. Falha de readiness ou backend obrigatorio down.
2. Degradacao sustentada.
3. Sinais normais.
4. Indisponibilidade de dados.

## Seguranca

O v1 deve seguir estes principios:

- diagnostico-only;
- `dry_run` sempre true;
- sem `command` livre;
- sem executor real;
- sem subprocess;
- sem SSH;
- sem Docker exec;
- futuras acoes somente em pipeline separado com allowlist e approval.

### O que isso impede

- remediacao automatica por acidente;
- remocao ou restart de recursos sem aprovacao;
- execucao de shell derivada de LLM;
- mistura de observacao com alteracao de estado.

## Testes necessarios

### Testes unitarios

- validação de schema para `AIOpsDiagnoseRequest`;
- validação de schema para `AIOpsDiagnoseResponse`;
- calculo de severidade para casos `ok`, `warning`, `critical` e `unknown`;
- mapeamento de checks para sinais;
- geracao de findings a partir de sinais conhecidos;
- geracao de recommended_actions em modo dry-run;
- bloqueio de qualquer tentativa de remediacao no v1.

### Testes de API

- `POST /v1/aiops/diagnose` retorna `200` com payload valido;
- `POST /v1/aiops/diagnose` rejeita request invalido;
- endpoint nao executa shell real;
- endpoint nao depende de executor local, SSH ou Docker;
- resposta inclui `dry_run: true`;
- auth continua funcionando como hoje.

### Lacunas que os testes devem cobrir

- nenhuma saida pode carregar comando executavel;
- nenhuma recomendacao pode virar acao automatica;
- metrics e readiness sao usados apenas como insumo;
- o legado continua passando `pytest` sem regressao.

## Plano incremental

### 1. `docs: add aiops v1 plan`

- registrar o desenho do v1 antes de codificar;
- alinhar escopo e nao-escopo;
- deixar claro que execucao real nao entra na fase 1.

### 2. `feat: add aiops schemas`

- adicionar schemas de request, response, signal, finding e recommended action;
- manter compatibilidade com os schemas atuais;
- nao alterar comportamento de runtime ainda.

### 3. `feat: add aiops diagnostic service`

- criar servico de diagnostico separado do orchestrator legado;
- ler readiness e metricas allowlisted;
- gerar findings e severidade;
- retornar recommended_actions em modo dry-run.

### 4. `feat: add /v1/aiops/diagnose`

- expor o endpoint diagnostico;
- proteger com auth atual;
- impedir execucao real;
- manter o legado intacto.

### 5. `test: expand aiops diagnostic coverage`

- adicionar testes unitarios e de API do v1;
- cobrir bloqueio de remediacao;
- cobrir severidade e saídas dry-run.

### 6. `docs: document aiops diagnostic endpoint`

- documentar request, response, checks e limites;
- explicitar que o endpoint nao executa comandos;
- explicar a separacao entre diagnostico e remediacao futura.

## Critérios de aceite

- `pytest` passa.
- `compileall` passa.
- o endpoint nao executa acoes.
- unsafe/remediation nao chama executor.
- `recommended_actions` sao apenas texto/dry-run.
- metricas e readiness sao usados de forma segura.

## Prioridade tecnica recomendada

### P0

- separar diagnostico de execucao;
- bloquear qualquer caminho do v1 que aceite command livre;
- definir schemas e contrato do endpoint.

### P1

- implementar service diagnostico;
- cobrir checks iniciais;
- adicionar testes de bloqueio e retorno.

### P2

- documentar o endpoint e os limites;
- preparar a futura trilha de remediacao, sem ativar ela.

## Riscos bloqueantes

- manter `orchestrator` como caminho principal do v1;
- reutilizar executores reais dentro do fluxo diagnostico;
- deixar `dry_run` opcional;
- permitir que `findings` carreguem pseudo-comandos;
- usar metricas sem allowlist;
- acoplar o v1 aos scripts de homelab, Proxmox, Grafana ou infra externa.

## Primeiro commit recomendado para implementacao

`docs: add aiops v1 plan`

Motivo:

- fixa o contrato do v1 antes de qualquer codigo;
- evita escopo escorregadio;
- permite revisar com clareza o que sera mantido, isolado e refatorado;
- nao altera runtime, entao e o menor passo seguro.

