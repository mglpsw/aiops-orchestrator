# GitHub Agent Review

Esta é a primeira versão do review on-demand acionado por comentário no PR.

## Uso

Comente em um Pull Request uma linha que comece com:

```text
/agent review
```

O workflow lê o evento do GitHub, consulta metadados e diff via API e comenta de volta
uma revisão determinística no próprio PR.

## Autorização

O agente só executa quando o autor do comentário tem uma destas permissões:

- `OWNER`
- `MEMBER`
- `COLLABORATOR`
- ou está listado em `AGENT_ALLOWED_USERS`

`AGENT_ALLOWED_USERS` aceita uma lista separada por vírgulas.

Se a pessoa não estiver autorizada, o bot responde com uma mensagem curta e não prossegue com a revisão.

## Escopo de análise

- Metadados do PR
- Arquivos alterados
- Patch/diff dos arquivos
- Check runs do commit analisado
- Comentários recentes do PR, quando disponíveis

## Classificação

- `P1` bloqueador: gatilhos e comandos perigosos, secrets hardcoded, perda de gates de segurança e falhas de CI
- `P2` importante: lacunas de cobertura, paths hardcoded em testes/CI, timeout ausente e documentação desalinhada
- `P3` sugestão: melhorias de clareza, organização e pequenas refatorações

## Segurança

O agente não faz checkout da branch do PR para executar código, não roda scripts do PR, não faz deploy e não mexe em containers.

## Evolução futura

O design foi mantido determinístico para facilitar a adição futura de um provider LLM sem mudar o contrato atual.
