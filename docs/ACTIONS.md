# AIOps Orchestrator — Catálogo de Actions

## Visão geral

O catálogo de actions (`config/actions.yaml`) define o conjunto explícito e allowlisted de operações
que o Action Planner pode sugerir. Nenhum comando livre é aceito fora deste catálogo.

**Fase atual (v1):** apenas ações `mode: readonly` e `risk: low` estão no catálogo.
Ações de escrita, restart, deploy ou remediação não existem nesta fase.

---

## Schema de uma action

```yaml
- action_id: string          # identificador único, snake_case
  description: string        # descrição legível por humanos
  command: string            # comando exato — sem interpolação livre
  mode: readonly             # readonly | readwrite (v1: somente readonly)
  risk: low                  # low | medium | high
  timeout_seconds: integer   # limite de execução em segundos
  requires_approval: boolean # true = aprovação humana obrigatória antes de executar
  tags: [string]             # categorias para filtragem e auditoria
```

### Campos obrigatórios

Todos os campos acima são obrigatórios. A ausência de qualquer um invalida a action e bloqueia o
deploy (validado por `scripts/validate_actions_catalog.sh`).

### Regras do catálogo

- `action_id` deve ser único no catálogo
- `mode: readonly` é o único valor permitido no v1
- `risk: low` é o único valor permitido no v1
- Nenhum comando pode conter padrões bloqueados (ver abaixo)
- Nenhuma interpolação livre de variáveis de usuário é permitida

### Padrões de comando bloqueados (validados automaticamente)

| Padrão           | Motivo do bloqueio                    |
| ---------------- | ------------------------------------- |
| `rm `            | Remoção de arquivos/diretórios        |
| `chmod 777`      | Permissão irrestrita                  |
| `docker exec`    | Execução dentro de container          |
| `ssh`            | Acesso remoto sem allowlist           |
| `curl \| bash`   | Execução remota de código (RCE)       |
| `\| bash`        | Pipe para shell (RCE)                 |
| `\| sh`          | Pipe para shell (RCE)                 |
| `git push`       | Alteração de repositório remoto       |
| `docker compose up` | Alteração de stack Docker          |
| `systemctl restart` | Reinício de serviço               |
| `systemctl start`   | Início de serviço                 |
| `systemctl stop`    | Parada de serviço                 |
| `systemctl disable` | Desabilitação de serviço          |

---

## Actions disponíveis no v1

### git_status

Exibe o estado atual da árvore de trabalho do repositório canônico.

- **Comando:** `git -C /opt/aiops-orchestrator status`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### git_diff_stat

Exibe estatísticas de diff em relação ao HEAD (sem patch completo).

- **Comando:** `git -C /opt/aiops-orchestrator diff --stat HEAD`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### git_log_recent

Exibe os 10 commits mais recentes em formato compacto.

- **Comando:** `git -C /opt/aiops-orchestrator log --oneline -10`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### docker_compose_config

Valida e exibe a configuração final do docker-compose (sem iniciar nada).

- **Comando:** `docker compose -f /opt/aiops-orchestrator/deploy/docker-compose.yml config`
- **Risk:** low | **Mode:** readonly | **Timeout:** 15s | **Approval:** false

### systemctl_status_aiops

Exibe o status da unit systemd do aiops-orchestrator (se gerenciado via systemd).

- **Comando:** `systemctl status aiops-orchestrator --no-pager`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_health_8000

Verifica o endpoint `/health` da produção estável (porta 8000).

- **Comando:** `curl -fsS http://127.0.0.1:8000/health`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_ready_8000

Verifica o endpoint `/ready` da produção estável (porta 8000).

- **Comando:** `curl -fsS http://127.0.0.1:8000/ready`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_health_8001

Verifica o endpoint `/health` do runtime next/observe (porta 8001).

- **Comando:** `curl -fsS http://127.0.0.1:8001/health`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### curl_ready_8001

Verifica o endpoint `/ready` do runtime next/observe (porta 8001).

- **Comando:** `curl -fsS http://127.0.0.1:8001/ready`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### journalctl_aiops_recent

Exibe as últimas 50 linhas do journal do aiops-orchestrator.

- **Comando:** `journalctl -u aiops-orchestrator -n 50 --no-pager`
- **Risk:** low | **Mode:** readonly | **Timeout:** 10s | **Approval:** false

### prometheus_query

Consulta o endpoint de query do Prometheus para a métrica `up` (health check básico).

- **Comando:** `curl -fsS 'http://192.168.3.200:9090/api/v1/query?query=up'`
- **Risk:** low | **Mode:** readonly | **Timeout:** 15s | **Approval:** false

---

## Validação do catálogo

Execute antes de qualquer commit que altere `config/actions.yaml`:

```bash
bash scripts/validate_actions_catalog.sh
```

O script verifica:

1. YAML parseável
2. `action_id` único no catálogo
3. Campos obrigatórios presentes e não-nulos
4. Nenhum padrão de comando bloqueado nos campos `command`

---

## Como adicionar uma nova action (futuro)

1. Definir `action_id` único e descritivo
2. Preencher todos os campos obrigatórios
3. Garantir que `command` não contenha padrões bloqueados
4. Para v1: manter `mode: readonly` e `risk: low`
5. Executar `scripts/validate_actions_catalog.sh` — deve passar sem erros
6. Abrir PR com justificativa de negócio para a nova action
7. Revisão humana obrigatória antes de merge

**Não adicionar actions de escrita, restart, deploy ou remediação sem aprovação explícita
do owner do repositório e atualização da fase (v1 → v2+).**
