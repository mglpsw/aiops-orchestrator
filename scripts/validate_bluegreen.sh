#!/usr/bin/env bash
# validate_bluegreen.sh — Validação do runtime blue/green
#
# ATENÇÃO: Este script pode iniciar o container aiops-orchestrator-next
# (blue/green) se ele não estiver rodando. Isso é uma operação mutável.
#
# Pré-requisito: defina AIOPS_BLUEGREEN_ALLOWED=1 para confirmar que você
# sabe que este script pode iniciar containers.
#
# Para validação puramente read-only (sem iniciar nada):
#   AIOPS_BLUEGREEN_READONLY=1 bash scripts/validate_bluegreen.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_BASE="$ROOT_DIR/deploy/docker-compose.yml"
COMPOSE_BG="$ROOT_DIR/deploy/docker-compose.bluegreen.yml"
COMPARE_SCRIPT="$ROOT_DIR/scripts/compare_aiops_runtimes.sh"
PROJECT_NAME="aiops-orchestrator-bluegreen"

compose() {
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_BASE" -f "$COMPOSE_BG" "$@"
}

wait_http() {
  local url="$1"
  local attempts="${2:-30}"
  local delay="${3:-2}"
  local i=1
  until curl -fsS "$url" >/dev/null 2>&1; do
    if [ "$i" -ge "$attempts" ]; then
      echo "Timed out waiting for $url" >&2
      return 1
    fi
    i=$((i + 1))
    sleep "$delay"
  done
}

assert_json() {
  local url="$1"
  local expr="$2"
  local body_file
  body_file="$(mktemp)"
  curl -fsS "$url" -o "$body_file"
  python3 - "$expr" "$body_file" <<'PY'
import json
import sys
from pathlib import Path

expr = sys.argv[1]
body_path = sys.argv[2]
data = json.loads(Path(body_path).read_text(encoding="utf-8"))

if expr == "ready":
    if data.get("ready") is not True:
        raise SystemExit("ready is not true")
elif expr == "diagnose":
    if not data.get("status") or not data.get("severity") or not data.get("summary"):
        raise SystemExit("diagnose payload missing basic fields")
    if data.get("dry_run") is not True:
        raise SystemExit("dry_run is not true")
else:
    raise SystemExit(f"unknown assertion {expr}")
PY
  local rc=$?
  rm -f "$body_file"
  return "$rc"
}

run_compare() {
  local tmp
  tmp="$(mktemp)"
  if bash "$COMPARE_SCRIPT" >"$tmp" 2>&1; then
    cat "$tmp"
    rm -f "$tmp"
    return 0
  fi

  local rc=$?
  cat "$tmp" >&2
  rm -f "$tmp"
  return "$rc"
}

next_container_running() {
  docker ps --format '{{.Names}}' | grep -qx 'aiops-orchestrator-next'
}

echo "Validating compose configuration (read-only)..."
compose config >/dev/null

READONLY_MODE="${AIOPS_BLUEGREEN_READONLY:-0}"

if next_container_running; then
  echo "Using existing aiops-orchestrator-next container on port 8001."
elif [ "$READONLY_MODE" = "1" ]; then
  echo "SKIP: aiops-orchestrator-next não está rodando."
  echo "      Em modo read-only, o script não inicia containers."
  echo "      Para iniciar, use: AIOPS_BLUEGREEN_ALLOWED=1 bash $0"
  exit 0
else
  ALLOWED="${AIOPS_BLUEGREEN_ALLOWED:-0}"
  if [ "$ALLOWED" != "1" ]; then
    echo "ERRO: aiops-orchestrator-next não está rodando e AIOPS_BLUEGREEN_ALLOWED não está definido." >&2
    echo "      Este script pode iniciar containers. Confirme com:" >&2
    echo "        AIOPS_BLUEGREEN_ALLOWED=1 bash $0" >&2
    echo "      Para validação read-only (sem iniciar): AIOPS_BLUEGREEN_READONLY=1 bash $0" >&2
    exit 1
  fi
  echo "Building and starting aiops-orchestrator-next on port 8001..."
  compose up -d --build aiops-orchestrator
fi

echo "Waiting for blue/green service health..."
wait_http "http://127.0.0.1:8001/health"
wait_http "http://127.0.0.1:8001/ready"

echo "Validating blue/green readiness payload..."
assert_json "http://127.0.0.1:8000/ready" "ready"
assert_json "http://127.0.0.1:8001/ready" "ready"

echo "Validating metrics endpoint..."
curl -fsS http://127.0.0.1:8001/metrics >/dev/null

echo "Checking legacy production service on 8000..."
wait_http "http://127.0.0.1:8000/health"
wait_http "http://127.0.0.1:8000/ready"

echo "Comparing legacy and next runtimes..."
run_compare

echo "Blue/green validation complete."
