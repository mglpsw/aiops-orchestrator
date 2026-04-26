# Blue/Green Deployment

Este guia documenta a operação blue/green do AIOps no repo canônico `/opt/aiops-orchestrator`.

## Topologia

- produção estável: `8000`
- next / observe: `8001`
- router: `agent-router` em `8010`
- projeto compose blue/green: `aiops-orchestrator-bluegreen`

## Como subir o ambiente blue/green

```bash
cd /opt/aiops-orchestrator
bash scripts/validate_bluegreen.sh
```

O override `deploy/docker-compose.bluegreen.yml` mantém:

- `container_name: aiops-orchestrator-next`
- `ports: 8001:8000`
- modo de observação no ambiente blue/green
- execução desabilitada no next
- autenticação desabilitada no next, para validação sem segredos

## Validação oficial

`scripts/validate_bluegreen.sh`:

- valida `docker compose config`
- reutiliza o runtime next existente quando ele já estiver vivo, ou sobe o override blue/green quando necessário
- verifica `8001`
- valida `8000`
- executa `scripts/compare_aiops_runtimes.sh`

## Comparação de runtimes

`scripts/compare_aiops_runtimes.sh`:

- compara `8000` vs `8001`
- classifica cada checagem como `PASS`, `WARN` ou `FAIL`
- não imprime tokens ou segredos
- pode ser executado isoladamente

## Regras de promoção

- promoção continua manual
- `FAIL` bloqueia a validação
- `WARN` não bloqueia por padrão
- use `STRICT=1` se quiser transformar `WARN` em falha

## Rollback seguro

- mantenha `8000` como produção
- pare apenas `8001` se necessário
- preserve volumes e dados
- não automatize troca de tráfego
