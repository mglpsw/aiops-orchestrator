# Orquestrador AIOps — Procedimentos de reversão

## Reversão rápida

### Parar serviço (sem perda de dados)
```bash
pct exec 102 -- bash -c "cd /opt/aiops/deploy && docker-compose down"
```
Isso para o container mas preserva todos os dados no volume `aiops-data`.

### Remoção completa (reversível)
```bash
# Parar serviço
pct exec 102 -- bash -c "cd /opt/aiops/deploy && docker-compose down"

# Remove image (optional - saves disk)
pct exec 102 -- docker rmi aiops-orchestrator 2>/dev/null || true

# Data volume is preserved. To fully remove:
# pct exec 102 -- docker volume rm aiops-data  # CAUTION: removes all task data
```

### Restore from Database Backup
```bash
# 1. List available backups
bash scripts/rollback.sh list

# 2. Stop service
bash scripts/rollback.sh stop

# 3. Restore specific backup
bash scripts/rollback.sh restore aiops-20260411_120000.db
```

### Restaurar configuração
```bash
# List config backups
pct exec 102 -- ls /opt/aiops/backups/*config*

# Extract config backup
pct exec 102 -- tar xzf /opt/aiops/backups/aiops-backup-TIMESTAMP-config.tar.gz -C /opt/aiops/

# Restart
pct exec 102 -- bash -c "cd /opt/aiops/deploy && docker-compose up -d"
```

## Partial Rollback Scenarios

### Revert Policy Changes
```bash
# Restore policies.yml from backup
pct exec 102 -- cp /opt/aiops/backups/config/policies.yml /opt/aiops/config/policies.yml
pct exec 102 -- docker restart aiops-orchestrator
```

### Revert to Previous Code Version
```bash
# Se usando git no homelab
cd /root/homelab
git log --oneline aiops/
git checkout <commit-hash> -- aiops/

# Reinstall
bash aiops/scripts/install.sh
```

## Impact Assessment

| Action | Impact | Reversible |
|--------|--------|------------|
| Stop service | No orchestration | Yes - restart |
| Remove container | No orchestration | Yes - rebuild |
| Remove volume | Lose task history | Only from backup |
| Remove /opt/aiops | Lose config | Only from backup |
| Remove image | Need rebuild | Yes - rebuild |

## Verification After Rollback

```bash
# Check service health
curl -s http://192.168.3.155:8000/health

# Check existing services unaffected
pct exec 102 -- docker ps --format "{{.Names}}: {{.Status}}" | grep -E "npm|nextcloud|open-webui"

# Run validation
bash scripts/validate.sh
```
