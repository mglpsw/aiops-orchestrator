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
#   - identidade CAEM 3.0 F0 pinada e generated views consistentes
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

# ── 4. Schemas v2 reproduzíveis ──────────────────────────────────────────────
header "4. Schemas AgentReview v2"
if python3 scripts/export-agent-review-v2-schemas.py --check; then
    ok "schemas v2 byte-identical"
else
    fail "schemas v2 divergentes — regenere no toolchain pinado"
fi

# ── 5. Identidade CAEM 3.0 F0 pinada ─────────────────────────────────────────
header "5. Identidade CAEM 3.0 F0"
if python3 scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check; then
    ok "pin válido e generated views consistentes"
else
    fail "pin ausente/incompleto ou generated views divergentes"
fi

# ── 6. RI-B0a.2 reuse/reference view ─────────────────────────────────────────
header "6. RI-B0a.2 reuse/reference view"
if python3 scripts/generate-ri-b0a-2-reuse-view.py --check; then
    ok "generated view em sincronia com o manifest"
else
    fail "docs/generated/RI_B0A_2_REUSE_REFERENCE.md desatualizado — regenere sem --check"
fi

# ── 7. Target-pack runtime authority view ────────────────────────────────────
header "7. Target-pack runtime authority view"
if python3 scripts/generate-target-pack-runtime-authority-view.py --check; then
    ok "docs/generated/target-pack-runtime-authority.v1.json em sincronia com as autoridades declaradas"
else
    fail "runtime authority view desatualizada — regenere com scripts/generate-target-pack-runtime-authority-view.py"
fi

# ── 8. Testes Python (unit, offline) ─────────────────────────────────────────
header "8. Testes Python"
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

# ── 9. Testes de subprocess git real (requires_network) ─────────────────────
# `requires_network` é a convenção já estabelecida deste repositório para
# "spawna um subprocess git real" (não acesso literal à rede -- ver o
# docstring do próprio test_diff_acquisition_v2.py), e a secção 7 acima a
# exclui por padrão. Uma auditoria adversarial da extração de conteúdo do
# AgentReview v2 (#200-B/#200-C) encontrou que isso deixava todo teste E2E
# real (redaction, losslessness de windowing, caminhos fail-closed de DLP)
# sem execução nesta gate, mesmo com `pytest -q` local (sem filtro de
# marker) sempre os executando e passando. Esta seção fecha essa lacuna sem
# tocar o escopo do filtro padrão da seção 7 (nenhuma dependência de
# docker/prometheus/runtime existe neste runner) -- roda somente os testes
# desse marker, isolados da execução padrão acima.
header "9. Testes de subprocess git real (requires_network)"
if ! command -v python3 &>/dev/null; then
    fail "python3 não encontrado"
else
    if python3 -m pytest -q -m requires_network --tb=short; then
        ok "todos os testes requires_network passaram"
    else
        fail "testes requires_network falharam"
    fi
fi

# ── 10. Ledger canônico -- invariantes estruturais (#324) ────────────────────
# Determinístico, offline, sem rede -- gate de CI. O modo --audit-live
# (não gateado, ver o próprio script) compara com o estado vivo do forge
# e é deliberadamente separado desta gate por P020 (gates proporcionais).
header "10. Ledger canônico -- invariantes estruturais"
if python3 scripts/lint-canonical-ledger.py --check; then
    ok "ledger canônico sem violação estrutural"
else
    fail "ledger canônico viola um invariante estrutural (#324) -- ver scripts/lint-canonical-ledger.py"
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
