# AIOps Blue/Green Operations

Este documento descreve a operação blue/green do AIOps no CT 102 usando o repo canônico `/opt/aiops-orchestrator`.

## Papéis dos runtimes

- `8000` é a produção estável.
- `8001` é o ambiente `next/observe` quando o override blue/green estiver habilitado.
- `8000` continua sendo o caminho produtivo.
- `8001` serve para observação, validação e comparação.

## Contrato operacional

### `8000`

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `POST /v1/aiops/diagnose`

### `8001`

- Mantém os mesmos endpoints de leitura e diagnóstico.
- Deve continuar sem promoção automática.
- Deve permanecer separado do caminho produtivo.

## Como validar paridade

Use o fluxo oficial:

```bash
cd /opt/aiops-orchestrator
bash scripts/validate_bluegreen.sh
```

O fluxo chama `scripts/compare_aiops_runtimes.sh` no final.

Também é possível executar o comparador isoladamente:

```bash
bash scripts/compare_aiops_runtimes.sh
```

## Classificação de resultados

- `PASS` = comportamento esperado.
- `WARN` = diferença observada, mas tolerada por padrão.
- `FAIL` = bloqueio de validação.

Por padrão, `WARN` não quebra a validação.

Se quiser endurecer a checagem, use:

```bash
STRICT=1 bash scripts/compare_aiops_runtimes.sh
```

## Critérios de pronto para promoção

- `8000` continua saudável.
- `8001` continua saudável.
- `ready` retorna `true` nos dois runtimes.
- `diagnose` responde com payload básico válido.
- não há regressão em métricas ou auth.
- os avisos observados são documentados e intencionais.

## Rollback seguro

- mantenha `8000` intacto;
- pare somente o `8001` se necessário;
- não remova volumes ou dados de produção;
- não automatize promoção nesta fase.

## Observações

- A promoção continua manual.
- O comparador não altera estado.
- Segredos, tokens e headers sensíveis não devem ser impressos.
