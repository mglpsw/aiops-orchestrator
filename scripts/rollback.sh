#!/usr/bin/env bash
# AIOps Orchestrator - Script de Rollback
# Reverte para um backup anterior ou para o serviço de forma segura
set -euo pipefail

TARGET_CT=102
BACKUP_DIR="/opt/aiops-orchestrator/backups"
ACTION="${1:-list}"

echo "=== AIOps — Rollback ==="

case "$ACTION" in
    list)
        echo "Backups disponíveis:"
        pct exec $TARGET_CT -- ls -lht "$BACKUP_DIR/" 2>/dev/null || echo "Nenhum backup encontrado"
        ;;
    stop)
        echo "Parando o AIOps Orchestrator..."
        pct exec $TARGET_CT -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose stop aiops-orchestrator"
        echo "Serviço parado. Volume de dados preservado."
        ;;
    restore)
        DB_BACKUP="${2:-}"
        if [ -z "$DB_BACKUP" ]; then
            echo "Uso: $0 restore <nome-do-backup>"
            echo "Disponíveis:"
            pct exec $TARGET_CT -- ls -t "$BACKUP_DIR/"aiops-*.db 2>/dev/null
            exit 1
        fi
        echo "Parando serviço..."
        pct exec $TARGET_CT -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose stop aiops-orchestrator"
        echo "Restaurando banco de dados de $DB_BACKUP..."
        pct exec $TARGET_CT -- bash -c "
            docker run --rm -v aiops-data:/data -v '$BACKUP_DIR':/backup alpine \
                cp '/backup/$DB_BACKUP' /data/aiops.db
        "
        echo "Reiniciando serviço..."
        pct exec $TARGET_CT -- bash -c "cd /opt/aiops-orchestrator/deploy && docker-compose up -d"
        echo "Rollback concluído. Verifique: curl http://192.168.3.155:8000/health"
        ;;
    *)
        echo "Uso: $0 {list|stop|restore <arquivo-de-backup>}"
        exit 1
        ;;
esac
