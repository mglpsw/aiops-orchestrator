#!/usr/bin/env bash
# AIOps Orchestrator - Testes de Fumaça
# Testa as funcionalidades principais incluindo aplicação de políticas
set -euo pipefail

BASE_URL="http://192.168.3.155:8000"
TOKEN=""
ERRORS=0

# Lê o token do .env na CT
if command -v pct &>/dev/null; then
    TOKEN=$(pct exec 102 -- grep AIOPS_API_TOKEN /opt/aiops-orchestrator/.env 2>/dev/null | cut -d= -f2 || echo "")
fi

if [ -z "$TOKEN" ]; then
    echo "Informe o token da API: "
    read -r TOKEN
fi

AUTH_HEADER="Authorization: Bearer $TOKEN"

echo "=== AIOps — Testes de Fumaça ==="
echo "Alvo: $BASE_URL"
echo ""

# Função auxiliar: executa um caso de teste e verifica o resultado esperado
test_case() {
    local name="$1"
    local expected="$2"
    shift 2
    echo -n "  Teste: $name ... "
    RESPONSE=$("$@" 2>/dev/null || echo "REQUEST_FAILED")
    if echo "$RESPONSE" | grep -q "$expected"; then
        echo "OK"
    else
        echo "FALHOU (esperado '$expected')"
        echo "    Resposta: ${RESPONSE:0:200}"
        ERRORS=$((ERRORS + 1))
    fi
}

echo "[1] Saúde e disponibilidade"
test_case "Health endpoint" "healthy" curl -sf "$BASE_URL/health"
test_case "Ready endpoint" "ready" curl -sf "$BASE_URL/ready"
test_case "Metrics endpoint" "aiops_tasks_total" curl -sf "$BASE_URL/metrics"

echo ""
echo "[2] Autenticação"
test_case "No token = 401" "401" curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/v1/tasks"
test_case "Bad token = 403" "403" curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer wrong" "$BASE_URL/v1/tasks"
test_case "Valid token = 200" "200" curl -s -o /dev/null -w "%{http_code}" -H "$AUTH_HEADER" "$BASE_URL/v1/tasks"

echo ""
echo "[3] Consulta segura (deve completar imediatamente)"
test_case "Query task" "task_id" curl -sf -X POST "$BASE_URL/v1/chat/ingest" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d '{"message": "What is the current date?", "user_id": "test"}'

echo ""
echo "[4] Ação bloqueada (reboot deve ser bloqueado pela política)"
test_case "Blocked: reboot" "blocked\|Blocked\|dangerous" curl -sf -X POST "$BASE_URL/v1/chat/ingest" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d '{"message": "reboot the proxmox host", "user_id": "test"}'

echo ""
echo "[5] Ação que requer aprovação"
test_case "Approval required" "approval\|awaiting\|plan" curl -sf -X POST "$BASE_URL/v1/chat/ingest" \
    -H "$AUTH_HEADER" -H "Content-Type: application/json" \
    -d '{"message": "restart the nextcloud container", "user_id": "test"}'

echo ""
echo "[6] Listagem de tarefas"
test_case "List tasks" "tasks" curl -sf -H "$AUTH_HEADER" "$BASE_URL/v1/tasks"

echo ""
echo "[7] Listagem de aprovações pendentes"
test_case "List approvals" "pending" curl -sf -H "$AUTH_HEADER" "$BASE_URL/v1/approvals"

echo ""
echo "[8] Status dos providers"
test_case "Provider status" "providers" curl -sf -H "$AUTH_HEADER" "$BASE_URL/v1/providers/status"

echo ""
echo "[9] Conectividade com AI Worker (192.168.3.87)"
AI_WORKER="192.168.3.87"
test_case "windows_exporter reachable" "windows" \
    curl -sf --connect-timeout 5 "http://$AI_WORKER:9182/metrics"
test_case "Ollama OLLAMA_HOST not localhost-only" "models" \
    curl -sf --connect-timeout 5 "http://$AI_WORKER:11434/api/tags"
test_case "Ollama /metrics reachable (v0.1.17+)" "ollama_\|go_" \
    curl -sf --connect-timeout 5 "http://$AI_WORKER:11434/metrics" || true
test_case "LHM exporter reachable" "lhm_\|libre_" \
    curl -sf --connect-timeout 5 "http://$AI_WORKER:9183/metrics" || true

echo ""
if [ $ERRORS -eq 0 ]; then
    echo "=== Todos os testes de fumaça passaram! ==="
else
    echo "=== $ERRORS teste(s) falharam ==="
    exit 1
fi
