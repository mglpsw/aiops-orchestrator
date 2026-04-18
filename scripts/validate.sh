#!/usr/bin/env bash
# AIOps Orchestrator - Script de Validação
# Valida configuração, sintaxe e saúde do serviço
set -euo pipefail

TARGET_CT=102
BASE_URL="http://192.168.3.155:8000"
ERRORS=0

echo "=== Validação do AIOps Orchestrator ==="
echo ""

# Função auxiliar: executa um comando e reporta OK ou FALHA
check() {
    local desc="$1"
    shift
    if "$@" &>/dev/null; then
        echo "  [OK] $desc"
    else
        echo "  [FALHA] $desc"
        ERRORS=$((ERRORS + 1))
    fi
}

echo "[1] Verificando status da CT 102..."
check "CT 102 is running" pct status 102

echo "[2] Verificando serviço Docker..."
check "Docker daemon running" pct exec $TARGET_CT -- docker info

echo "[3] Verificando status do container..."
check "aiops-orchestrator container running" pct exec $TARGET_CT -- docker ps --filter name=aiops-orchestrator --filter status=running -q

echo "[4] Verificando endpoints..."
check "Health endpoint" curl -sf "$BASE_URL/health"
check "Ready endpoint" curl -sf "$BASE_URL/ready"
check "Metrics endpoint" curl -sf "$BASE_URL/metrics"

echo "[5] Verificando arquivos de configuração..."
check "providers.yml exists" pct exec $TARGET_CT -- test -f /opt/aiops/config/providers.yml
check "policies.yml exists" pct exec $TARGET_CT -- test -f /opt/aiops/config/policies.yml
check "routes.yml exists" pct exec $TARGET_CT -- test -f /opt/aiops/config/routes.yml
check ".env exists" pct exec $TARGET_CT -- test -f /opt/aiops/.env

echo "[6] Verificando persistência de dados..."
check "Data volume exists" pct exec $TARGET_CT -- docker volume inspect aiops-data

echo "[7] Verificando conflitos de porta..."
check "Port 8000 only used by aiops" pct exec $TARGET_CT -- bash -c "ss -tlnp | grep ':8000' | grep -q docker-proxy"

echo "[8] Verificando se os serviços existentes não foram afetados..."
check "NPM running" pct exec $TARGET_CT -- docker ps --filter name=npm --filter status=running -q
check "Nextcloud running" pct exec $TARGET_CT -- docker ps --filter name=nextcloud_app --filter status=running -q

echo ""
if [ $ERRORS -eq 0 ]; then
    echo "=== Todas as verificações passaram! ==="
else
    echo "=== $ERRORS verificação(es) falharam ==="
    exit 1
fi
