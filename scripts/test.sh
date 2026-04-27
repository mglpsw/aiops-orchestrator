#!/usr/bin/env bash
# AIOps Orchestrator — Entrypoint canônico de testes
#
# Uso:
#   bash scripts/test.sh              # todos os testes unit
#   bash scripts/test.sh -k foo       # filtro por nome
#   bash scripts/test.sh --co         # só collect (não executa)
#   AIOPS_INTEGRATION=1 bash scripts/test.sh  # inclui testes de integração
#
# Não requer Docker, Prometheus, Ollama nem secrets reais.
# Variáveis de ambiente são faked pelos conftest/defaults dos testes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

INTEGRATION="${AIOPS_INTEGRATION:-0}"

PYTEST_ARGS=(-q --tb=short)

if [ "$INTEGRATION" != "1" ]; then
    PYTEST_ARGS+=(-m "not integration and not requires_runtime and not requires_docker and not requires_prometheus and not requires_network")
fi

PYTEST_ARGS+=("$@")

echo "=== AIOps — Testes unitários ==="
echo "Diretório: $ROOT_DIR"
echo "Python: $(python3 --version)"
if [ "$INTEGRATION" = "1" ]; then
    echo "Modo: unit + integration"
else
    echo "Modo: unit (sem Docker/Prometheus/rede)"
fi
echo ""

exec python3 -m pytest "${PYTEST_ARGS[@]}"
