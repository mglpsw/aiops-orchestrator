#!/usr/bin/env bash
# AIOps Orchestrator — Validação do Catálogo de Actions
# Verifica que config/actions.yaml é seguro antes de qualquer deploy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CATALOG="$ROOT_DIR/config/actions.yaml"
ERRORS=0

err() { echo "  [ERRO] $*"; ERRORS=$((ERRORS + 1)); }
ok()  { echo "  [OK] $*"; }

echo "=== Validação do Catálogo de Actions ==="
echo "Arquivo: $CATALOG"
echo ""

if [ ! -f "$CATALOG" ]; then
    err "Arquivo não encontrado: $CATALOG"
    exit 1
fi
ok "Arquivo encontrado"

# Validação via Python: YAML parse + campos obrigatórios + padrões bloqueados
python3 - "$CATALOG" <<'PY'
import sys
import pathlib
import re

catalog_path = pathlib.Path(sys.argv[1])
errors = 0

try:
    import yaml
except ImportError:
    print("  [ERRO] PyYAML não disponível — execute: pip install pyyaml")
    sys.exit(1)

def err(msg):
    global errors
    print(f"  [ERRO] {msg}")
    errors += 1

def ok(msg):
    print(f"  [OK] {msg}")

# 1. YAML parseável
try:
    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
except yaml.YAMLError as e:
    err(f"YAML inválido: {e}")
    sys.exit(1)

ok("YAML parseável")

if not isinstance(data, dict) or "catalog" not in data:
    err("Estrutura inválida: chave 'catalog' não encontrada")
    sys.exit(1)

catalog = data.get("catalog")
if not isinstance(catalog, list) or len(catalog) == 0:
    err("Catálogo vazio ou inválido")
    sys.exit(1)

ok(f"{len(catalog)} action(s) no catálogo")

# 2. Campos obrigatórios + action_id único
required_fields = ["action_id", "risk", "mode", "timeout_seconds", "requires_approval"]
seen_ids: dict = {}

for i, action in enumerate(catalog):
    action_id = action.get("action_id", f"<item {i}>")

    if action_id in seen_ids:
        err(f"action_id duplicado: '{action_id}' (itens {seen_ids[action_id]} e {i})")
    else:
        seen_ids[action_id] = i

    for field in required_fields:
        if field not in action:
            err(f"'{action_id}': campo obrigatório ausente: '{field}'")
        elif action[field] is None:
            err(f"'{action_id}': campo '{field}' é null")

ok("action_ids únicos e campos obrigatórios presentes")

# 3. Padrões de comando bloqueados
blocked_patterns = [
    (r'\brm\s',              "rm (remoção de arquivos)"),
    (r'chmod\s+777',         "chmod 777 (permissão irrestrita)"),
    (r'docker\s+exec\b',     "docker exec (execução em container)"),
    (r'(?<!\w)ssh\b',        "ssh (acesso remoto)"),
    (r'\|\s*bash\b',         "pipe para bash (RCE)"),
    (r'\|\s*sh\b',           "pipe para sh (RCE)"),
    (r'curl\s.*\|\s*\w*sh',  "curl pipe shell (RCE)"),
    (r'git\s+push\b',        "git push (alteração remota)"),
    (r'docker[\s-]compose\s+up\b', "docker compose up (alteração de stack)"),
    (r'systemctl\s+restart\b',     "systemctl restart"),
    (r'systemctl\s+start\b',       "systemctl start"),
    (r'systemctl\s+stop\b',        "systemctl stop"),
    (r'systemctl\s+disable\b',     "systemctl disable"),
]

for i, action in enumerate(catalog):
    action_id = action.get("action_id", f"<item {i}>")
    command = action.get("command", "")
    if not command:
        continue
    for pattern, label in blocked_patterns:
        if re.search(pattern, command):
            err(f"'{action_id}': padrão bloqueado detectado [{label}] em: {command!r}")

if errors == 0:
    ok("Nenhum padrão de comando bloqueado detectado")
    sys.exit(0)
else:
    sys.exit(1)
PY

PYTHON_RC=$?

echo ""
if [ $PYTHON_RC -eq 0 ] && [ $ERRORS -eq 0 ]; then
    echo "=== Catálogo de actions válido ==="
else
    ERRORS=$((ERRORS + 1))
    echo "=== ERROS no catálogo de actions — ver mensagens acima ==="
    exit 1
fi
