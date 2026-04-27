#!/usr/bin/env bash
# AIOps Orchestrator — Validação local (read-only)
#
# Execute DENTRO do CT 102, não no host Proxmox.
# Não inicia, para ou reinicia nenhum serviço.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="http://127.0.0.1:8000"
ERRORS=0

echo "=== Validação do AIOps Orchestrator (local, read-only) ==="
echo "Executando em: $(hostname) — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

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

echo "[1] Docker daemon..."
check "Docker daemon acessível" docker info

echo "[2] Container AIOps..."
check "aiops-orchestrator em execução" \
    bash -c "docker ps --filter name='^aiops-orchestrator$' --filter status=running -q | grep -q ."
check "Project name é aiops-orchestrator (não 'deploy')" \
    bash -c "docker inspect aiops-orchestrator --format '{{index .Config.Labels \"com.docker.compose.project\"}}' 2>/dev/null | grep -qx 'aiops-orchestrator'"

echo "[3] Endpoints HTTP..."
check "Health endpoint" curl -sf --max-time 5 "$BASE_URL/health"
check "Ready endpoint" curl -sf --max-time 5 "$BASE_URL/ready"
check "Metrics endpoint" curl -sf --max-time 5 "$BASE_URL/metrics"

echo "[4] Arquivos de configuração..."
check "config/actions.yaml existe" test -f "$ROOT_DIR/config/actions.yaml"
check "config/providers.yml existe" test -f "$ROOT_DIR/config/providers.yml"
check "config/policies.yml existe" test -f "$ROOT_DIR/config/policies.yml"
check ".env existe" test -f "$ROOT_DIR/.env"

echo "[5] Compose config (sem iniciar nada)..."
check "docker-compose.yml válido" \
    docker compose -p aiops-orchestrator -f "$ROOT_DIR/deploy/docker-compose.yml" config --quiet
check "docker-compose.yml tem name: aiops-orchestrator" \
    bash -c "docker compose -p aiops-orchestrator -f '$ROOT_DIR/deploy/docker-compose.yml' config 2>/dev/null | grep -q 'name: aiops-orchestrator'"

echo "[6] Persistência..."
check "Volume aiops-data existe" docker volume inspect aiops-data

echo "[7] docker-compose.maintenance.yml não montado em produção..."
check "docker.sock NÃO montado no container de produção" \
    bash -c "! docker inspect aiops-orchestrator --format '{{json .Mounts}}' 2>/dev/null | grep -q 'docker.sock'"

echo "[8] Catálogo de actions (read-only)..."
CATALOG_SCRIPT="$ROOT_DIR/scripts/validate_actions_catalog.sh"
if [ -f "$CATALOG_SCRIPT" ]; then
    if bash "$CATALOG_SCRIPT" &>/dev/null; then
        echo "  [OK] Catálogo de actions válido"
    else
        echo "  [FALHA] Catálogo de actions inválido"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  [SKIP] validate_actions_catalog.sh não encontrado"
fi

echo ""
if [ $ERRORS -eq 0 ]; then
    echo "=== Todas as verificações passaram ==="
    exit 0
else
    echo "=== $ERRORS verificação(ões) falharam ==="
    exit 1
fi
