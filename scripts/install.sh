#!/usr/bin/env bash
# AIOps Orchestrator - Script de Instalação
# Instala o orquestrador na CT Docker (102) em /opt/aiops-orchestrator
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TARGET_CT=102
TARGET_PATH="/opt/aiops-orchestrator"

echo "=== AIOps Orchestrator — Instalador ==="
echo "Origem : $PROJECT_DIR"
echo "Destino: CT $TARGET_CT -> $TARGET_PATH"
echo ""

# Verifica se está rodando no host Proxmox
if ! command -v pct &>/dev/null; then
    echo "ERRO: pct não encontrado. Execute este script no host Proxmox."
    exit 1
fi

# Verifica se a CT está rodando
if ! pct status $TARGET_CT | grep -q running; then
    echo "ERRO: CT $TARGET_CT não está em execução."
    exit 1
fi

# Cria o diretório de destino na CT
echo "[1/6] Criando diretório de destino..."
pct exec $TARGET_CT -- mkdir -p "$TARGET_PATH"/{data,config,deploy}

# Copia os arquivos do projeto
echo "[2/6] Copiando arquivos do projeto..."
pct push $TARGET_CT "$PROJECT_DIR/requirements.txt" "$TARGET_PATH/requirements.txt"
pct push $TARGET_CT "$PROJECT_DIR/deploy/Dockerfile" "$TARGET_PATH/deploy/Dockerfile"
pct push $TARGET_CT "$PROJECT_DIR/deploy/docker-compose.yml" "$TARGET_PATH/deploy/docker-compose.yml"
if [ -f "$PROJECT_DIR/deploy/docker-compose.bluegreen.yml" ]; then
    pct push $TARGET_CT "$PROJECT_DIR/deploy/docker-compose.bluegreen.yml" "$TARGET_PATH/deploy/docker-compose.bluegreen.yml"
fi

# Cria subdiretórios do app na CT
for dir in app app/api app/core app/adapters app/policies app/models app/services app/utils app/agent_router; do
    pct exec $TARGET_CT -- mkdir -p "$TARGET_PATH/$dir"
done

find "$PROJECT_DIR/app" -name "*.py" | while read -r f; do
    rel="${f#$PROJECT_DIR/}"
    pct push $TARGET_CT "$f" "$TARGET_PATH/$rel"
done

# Copia arquivos de configuração (.yml)
for f in "$PROJECT_DIR/config/"*.yml; do
    fname="$(basename "$f")"
    pct push $TARGET_CT "$f" "$TARGET_PATH/config/$fname"
done

# Copia o arquivo de exemplo de variáveis de ambiente
pct push $TARGET_CT "$PROJECT_DIR/.env.example" "$TARGET_PATH/.env.example"

# Cria .env a partir do exemplo, se ainda não existir
echo "[3/6] Configurando ambiente..."
pct exec $TARGET_CT -- bash -c "
    if [ ! -f '$TARGET_PATH/.env' ]; then
        cp '$TARGET_PATH/.env.example' '$TARGET_PATH/.env'
        # Gera token aleatório e substitui o placeholder
        TOKEN=\$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
        sed -i "s/changeme-generate-a-secure-token/\$TOKEN/" '$TARGET_PATH/.env'
        echo '.env criado com token de API gerado automaticamente'
        echo "Token da API: \$TOKEN"
        echo 'SALVE ESTE TOKEN — será necessário para configurar a integração com o WebUI.'
    else
        echo '.env already exists, skipping'
    fi
"

# Constrói a imagem Docker
echo "[4/6] Construindo imagem Docker..."
pct exec $TARGET_CT -- bash -c "cd '$TARGET_PATH/deploy' && docker-compose build"

# Sobe o serviço
echo "[5/6] Iniciando serviço..."
pct exec $TARGET_CT -- bash -c "cd '$TARGET_PATH/deploy' && docker-compose up -d"

# Aguarda health check ficar disponível
echo "[6/6] Aguardando health check..."
for i in $(seq 1 30); do
    if pct exec $TARGET_CT -- curl -sf http://localhost:8000/health &>/dev/null; then
        echo "AIOps Orchestrator está saudável!"
        pct exec $TARGET_CT -- curl -s http://localhost:8000/health | python3 -m json.tool
        echo ""
        echo "=== Instalação Concluída ==="
        echo "Serviço: http://192.168.3.155:8000"
        echo "Health:  http://192.168.3.155:8000/health"
        echo "Ready:   http://192.168.3.155:8000/ready"
        echo ""
        echo "Próximos passos:"
        echo "1. Edite /opt/aiops-orchestrator/.env na CT 102 para definir as chaves de API"
        echo "2. Configure a integração com o WebUI (veja docs/INTEGRATIONS.md)"
        echo "3. Execute: bash scripts/smoke_test.sh"
        exit 0
    fi
    sleep 2
done

echo "WARNING: Service did not become healthy within 60s."
echo "Check logs: pct exec $TARGET_CT -- docker logs aiops-orchestrator"
exit 1
