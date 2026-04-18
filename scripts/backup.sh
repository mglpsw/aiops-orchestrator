#!/usr/bin/env bash
# AIOps Orchestrator - Script de Backup
set -euo pipefail

TARGET_CT=102
BACKUP_DIR="/opt/aiops/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="aiops-backup-$TIMESTAMP"

echo "=== AIOps — Backup ==="
echo "Timestamp: $TIMESTAMP"

pct exec $TARGET_CT -- bash -c "
    mkdir -p '$BACKUP_DIR'

    # Faz backup do banco de dados
    if docker exec aiops-orchestrator test -f /app/data/aiops.db; then
        docker cp aiops-orchestrator:/app/data/aiops.db '$BACKUP_DIR/aiops-$TIMESTAMP.db'
        echo 'Banco de dados salvo'
    fi

    # Faz backup da configuração
    tar czf '$BACKUP_DIR/${BACKUP_NAME}-config.tar.gz' -C /opt/aiops config/ .env 2>/dev/null || true
    echo 'Configuração salva'

    # Remove backups antigos (mantém os últimos 10)
    cd '$BACKUP_DIR'
    ls -t aiops-*.db 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
    ls -t aiops-backup-*-config.tar.gz 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true

    echo 'Backup concluído: $BACKUP_DIR'
    ls -lh '$BACKUP_DIR/'
"
