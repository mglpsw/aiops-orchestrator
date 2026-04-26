# Orquestrador AIOps — Resolução de problemas

## Problemas comuns

### Serviço não inicia

**Sintoma**: Container encerra imediatamente ou falha na verificação de saúde.

```bash
# Verificar logs
pct exec 102 -- docker logs aiops-orchestrator

# Causas comuns:
# 1. Arquivo .env ausente
pct exec 102 -- test -f /opt/aiops-orchestrator/.env && echo "OK" || echo "MISSING"

# 2. Port 8000 already in use
pct exec 102 -- ss -tlnp | grep 8000

# 3. Permission issue on data volume
pct exec 102 -- docker exec aiops-orchestrator ls -la /app/data/
```

### 401 Unauthorized

**Sintoma**: Todas as chamadas da API retornam 401.

```bash
# Verificar token is set
pct exec 102 -- grep AIOPS_API_TOKEN /opt/aiops-orchestrator/.env

# Test with token
TOKEN=$(pct exec 102 -- grep AIOPS_API_TOKEN /opt/aiops-orchestrator/.env | cut -d= -f2)
curl -H "Authorization: Bearer $TOKEN" http://192.168.3.155:8000/v1/tasks
```

### Provider Unhealthy

**Sintoma**: Tarefas falham com erros de provedor.

```bash
# Check all providers
TOKEN=...
curl -H "Authorization: Bearer $TOKEN" http://192.168.3.155:8000/v1/providers/status | python3 -m json.tool

# Test Ollama directly
curl http://192.168.3.87:11434/api/tags

# Test Claude API key
curl -H "x-api-key: YOUR_KEY" -H "anthropic-version: 2023-06-01" \
    https://api.anthropic.com/v1/messages \
    -d '{"model":"claude-sonnet-4-20250514","max_tokens":1,"messages":[{"role":"user","content":"hi"}]}'
```

### Tasks Stuck in "executing"

**Sintoma**: Tarefas nunca completam.

```bash
# Check for stuck tasks
pct exec 102 -- docker exec aiops-orchestrator python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/aiops.db')
for row in conn.execute('SELECT id, message, updated_at FROM tasks WHERE status=\"executing\"'):
    print(row)
"

# If a task is truly stuck, manually fail it:
# (via API) POST /v1/tasks/{id} with status update
# (via DB) UPDATE tasks SET status='failed' WHERE id='...'
```

### Banco de dados bloqueado

**Sintoma**: Erros "database is locked" do SQLite.

```bash
# This can happen with concurrent writes to SQLite
# Solution: restart the service
pct exec 102 -- docker restart aiops-orchestrator
```

### Cannot Connect to Ollama

**Sintoma**: Classificação falha, provedor aparece como indisponível.

```bash
# Verify Ollama is running on your PC
curl http://192.168.3.87:11434/api/tags

# Verify network from CT 102
pct exec 102 -- curl -m 5 http://192.168.3.87:11434/api/tags

# If Ollama has CORS/binding issues, ensure it listens on 0.0.0.0
# On the Ollama PC: OLLAMA_HOST=0.0.0.0 ollama serve
```

### High Memory Usage

```bash
# Check container resource usage
pct exec 102 -- docker stats aiops-orchestrator --no-stream

# If excessive, check for task accumulation
pct exec 102 -- docker exec aiops-orchestrator python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/aiops.db')
print(conn.execute('SELECT COUNT(*) FROM tasks').fetchone())
print(conn.execute('SELECT COUNT(*) FROM audit_log').fetchone())
"
```

## Análise de logs

### View Structured Logs
```bash
# All logs
pct exec 102 -- docker logs aiops-orchestrator 2>&1 | python3 -m json.tool

# Filter by level
pct exec 102 -- docker logs aiops-orchestrator 2>&1 | grep '"level": "ERROR"'

# Filter by task
pct exec 102 -- docker logs aiops-orchestrator 2>&1 | grep '"task_id": "abc123"'

# Recent errors only
pct exec 102 -- docker logs --since 1h aiops-orchestrator 2>&1 | grep ERROR
```

## Reset Procedures

### Reset Database (Fresh Start)
```bash
# Backup first!
bash scripts/backup.sh

# Remove and recreate volume
pct exec 102 -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose down"
pct exec 102 -- docker volume rm aiops-data
pct exec 102 -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose up -d"
```

### Resetar configuração
```bash
pct exec 102 -- cp /opt/aiops-orchestrator/.env.example /opt/aiops-orchestrator/.env
# Re-generate token
pct exec 102 -- bash -c "TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))'); sed -i \"s/changeme-generate-a-secure-token/\$TOKEN/\" /opt/aiops-orchestrator/.env"
pct exec 102 -- docker restart aiops-orchestrator
```
