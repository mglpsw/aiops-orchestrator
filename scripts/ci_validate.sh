#!/usr/bin/env bash
# AIOps Orchestrator — Validação CI (repo-only, sem runtime)
#
# Projetado para GitHub Actions e agents remotos.
# NÃO requer: Docker daemon, container em produção, systemd, CT 102,
#              Prometheus, Ollama, rede externa, secrets reais.
#
# O que valida:
#   - sintaxe bash de todos os scripts
#   - catálogo de actions (YAML + guardrails)
#   - compose syntax (config --quiet, sem daemon)
#   - testes Python unitários offline
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ERRORS=0

header() { echo ""; echo "=== $* ==="; }
ok()     { echo "  [OK] $*"; }
fail()   { echo "  [FALHA] $*"; ERRORS=$((ERRORS + 1)); }
skip()   { echo "  [SKIP] $*"; }

header "CI — Validação de repositório (offline)"
echo "Diretório : $ROOT_DIR"
echo "Python    : $(python3 --version 2>&1)"
echo "Data      : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── 1. Sintaxe bash ──────────────────────────────────────────────────────────
header "1. Sintaxe bash"
while IFS= read -r -d '' script; do
    if bash -n "$script" 2>/dev/null; then
        ok "$(basename "$script")"
    else
        fail "$(basename "$script") — erro de sintaxe"
        bash -n "$script" || true
    fi
done < <(find scripts -name '*.sh' -print0)

# ── 2. Catálogo de actions ────────────────────────────────────────────────────
header "2. Catálogo de actions"
if bash scripts/validate_actions_catalog.sh; then
    ok "catálogo válido"
else
    fail "catálogo inválido"
fi

# ── 3. Docker Compose syntax (sem daemon) ────────────────────────────────────
header "3. Docker Compose syntax"
if command -v docker compose &>/dev/null || command -v docker-compose &>/dev/null; then
    # Cria .env mínimo se não existir (CI não tem .env real)
    if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$ROOT_DIR/.env.example" ]; then
        cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
        CREATED_ENV=1
    else
        CREATED_ENV=0
    fi

    if docker compose -p aiops-orchestrator \
            -f "$ROOT_DIR/deploy/docker-compose.yml" config --quiet 2>/dev/null; then
        ok "docker-compose.yml válido"
    else
        fail "docker-compose.yml inválido"
    fi

    if docker compose -p aiops-orchestrator \
            -f "$ROOT_DIR/deploy/docker-compose.yml" \
            -f "$ROOT_DIR/deploy/docker-compose.bluegreen.yml" config --quiet 2>/dev/null; then
        ok "docker-compose.bluegreen.yml válido"
    else
        fail "docker-compose.bluegreen.yml inválido"
    fi

    # Limpa .env temporário
    if [ "${CREATED_ENV:-0}" = "1" ]; then
        rm -f "$ROOT_DIR/.env"
    fi
else
    skip "docker compose não disponível — pulando validação de compose"
fi

# ── 4. Testes Python (unit, offline) ─────────────────────────────────────────
header "4. Testes Python"
if ! command -v python3 &>/dev/null; then
    fail "python3 não encontrado"
elif ! python3 -m pytest --version &>/dev/null; then
    fail "pytest não instalado — rode: pip install -r requirements-dev.txt"
else
    if python3 -m pytest -q \
            -m "not integration and not requires_runtime and not requires_docker and not requires_prometheus and not requires_network" \
            --tb=short; then
        ok "todos os testes unitários passaram"
    else
        fail "testes falharam"
    fi
fi

# ── Resultado ────────────────────────────────────────────────────────────────
echo ""
if [ $ERRORS -eq 0 ]; then
    echo "=== CI validation: OK ==="
    exit 0
else
    echo "=== CI validation: $ERRORS falha(s) ==="
    exit 1
fi
