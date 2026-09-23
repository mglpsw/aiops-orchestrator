# Perfil — transactional-writer

**Aplicação:** Mutação persistente.

**Status:** adaptação proposta MPACK; ativação por decisão do repositório. Fontes: [S01, S09].

## Obrigações a concretizar

Nomear writer e transação, estado fresco, recursos/locks se materiais, commit/rollback, efeitos e auditoria. Usar o banco real quando a claim depender de suas garantias.

## Controle mínimo sugerido

Corrida ou estado stale deve respeitar o contrato sem escrita parcial indevida.

## Delimitação

Selecione somente obrigações materiais, nomeie comandos e owners locais, registre N/A justificado. Este perfil não enumera todos os deveres de todos os produtos e não fornece verdict automático. Uma falha do ambiente impede evidência satisfatória, mas não é automaticamente defeito do produto.
