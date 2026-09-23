# Perfil — external-effects

**Aplicação:** Notificação, provider ou efeito fora do processo.

**Status:** adaptação proposta MPACK; ativação por decisão do repositório. Fontes: [S09].

## Obrigações a concretizar

Nomear autorização, sandbox, idempotência, retry, estados observáveis e boundary de envio. Providers reais não são habilitados pelo pack.

## Controle mínimo sugerido

Tentar novamente não pode duplicar efeito quando o contrato promete idempotência; sent não equivale a delivered.

## Delimitação

Selecione somente obrigações materiais, nomeie comandos e owners locais, registre N/A justificado. Este perfil não enumera todos os deveres de todos os produtos e não fornece verdict automático. Uma falha do ambiente impede evidência satisfatória, mas não é automaticamente defeito do produto.
