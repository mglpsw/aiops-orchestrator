# Orquestrador AIOps — Guia de operações

## Gerenciamento de serviços

Todos os comandos são executados a partir do **host Proxmox**.

### Estado
```bash
# Estado do container
pct exec 102 -- docker ps --filter name=aiops-orchestrator

# Verificação de saúde
curl -s http://192.168.3.155:8000/health | python3 -m json.tool

# Prontidão
curl -s http://192.168.3.155:8000/ready | python3 -m json.tool

# Logs (last 100 lines)
pct exec 102 -- docker logs --tail 100 aiops-orchestrator

# Follow logs
pct exec 102 -- docker logs -f aiops-orchestrator
```

### Start / Stop / Restart
```bash
# Start
pct exec 102 -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose up -d"

# Stop (preserves data)
pct exec 102 -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose down"

# Restart
pct exec 102 -- docker restart aiops-orchestrator

# Rebuild and restart (after code changes)
pct exec 102 -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose up -d --build"
```

### Validate
```bash
bash /opt/aiops-orchestrator/scripts/validate.sh
```

GitHub Actions CI mirrors these validations on push and pull request, but it never deploys.
Deployments stay manual and approved.

Legacy chat/task/provider surfaces remain compatible for now, but they are deprecated and emit
deprecation headers plus lightweight usage metrics. New work should target the canonical
`/v1/aiops/*` APIs.

### Teste de fumaça
```bash
bash /opt/aiops-orchestrator/scripts/smoke_test.sh
```

### Backup
```bash
bash /opt/aiops-orchestrator/scripts/backup.sh
```

### Reversão
```bash
# Listar backups disponíveis
bash /opt/aiops-orchestrator/scripts/rollback.sh list

# Stop service safely
bash /opt/aiops-orchestrator/scripts/rollback.sh stop

# Restore from backup
bash /opt/aiops-orchestrator/scripts/rollback.sh restore aiops-20260411_120000.db
```

## Alterações de configuração

### Edit Environment
```bash
pct exec 102 -- nano /opt/aiops-orchestrator/.env
pct exec 102 -- docker restart aiops-orchestrator
```

### Edit Policies
```bash
pct exec 102 -- nano /opt/aiops-orchestrator/config/policies.yml
pct exec 102 -- docker restart aiops-orchestrator
```

### Edit Provider Routes
```bash
pct exec 102 -- nano /opt/aiops-orchestrator/config/routes.yml
pct exec 102 -- docker restart aiops-orchestrator
```

## Monitoramento

### Prometheus Metrics
```bash
curl -s http://192.168.3.155:8000/metrics
```

### Key Metrics
- `aiops_tasks_total` - Total tasks processed
- `aiops_tasks_by_status{status="..."}` - Tasks by status
- `aiops_provider_calls_total` - LLM API calls made
- `aiops_provider_failures_total` - Provider failures
- `aiops_approvals_pending` - Tasks awaiting approval
- `aiops_blocked_actions_total` - Actions blocked by policy

### Banco de dados Inspection
```bash
pct exec 102 -- docker exec aiops-orchestrator python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/aiops.db')
cursor = conn.cursor()
cursor.execute('SELECT status, COUNT(*) FROM tasks GROUP BY status')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]}')
conn.close()
"
```

## Troubleshooting Quick Reference

| Problem | Check |
|---------|-------|
| Service won't start | `pct exec 102 -- docker logs aiops-orchestrator` |
| 401 Unauthorized | Verify `AIOPS_API_TOKEN` in `.env` matches client |
| Provider unhealthy | `curl -H "..." .../v1/providers/status` |
| Task stuck | Check DB: `SELECT * FROM tasks WHERE status='executing'` |
| No Ollama connection | Verify Ollama running on 192.168.3.87:11434 |
| Port conflict | `pct exec 102 -- ss -tlnp \| grep 8000` |
