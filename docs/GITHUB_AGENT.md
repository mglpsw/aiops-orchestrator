# GitHub Agent Review

Esta primeira versão suporta dois modos:

- `/agent review` para revisão determinística
- `/agent review llm` para revisão determinística com apoio opcional do Agent Router

## Uso

Comente em um Pull Request uma linha que comece com um dos comandos acima.

O workflow lê o evento do GitHub, consulta metadados e diff via API e publica uma revisão no próprio PR.

## Autorização

O agent só executa quando o autor do comentário tem uma destas permissões:

- `OWNER`
- `MEMBER`
- `COLLABORATOR`
- ou está listado em `AGENT_ALLOWED_USERS`

`AGENT_ALLOWED_USERS` aceita uma lista separada por vírgulas em `Settings -> Secrets and variables -> Actions`.

Se a pessoa não estiver autorizada, o bot responde com uma mensagem curta e encerra.

## Modo determinístico

O review determinístico:

- não executa código do PR
- não faz checkout da branch do PR para execução
- não faz deploy
- não opera containers
- não chama o Agent Router

Ele prioriza achados P1 e P2, limita a saída a no máximo 5 achados principais e evita comentários longos.

### Interpretação

- `P1` bloqueador: risco de segurança, execução perigosa, perda de gates, CI quebrado
- `P2` importante: lacuna de cobertura, path hardcoded, timeout ausente, docs ou config desalinhados
- `P3` sugestão: melhorias de clareza e pequenos ajustes

Se não houver P1/P2 determinísticos, o comentário fica curto e diz exatamente isso.

## Modo LLM

`/agent review llm` usa o Agent Router apenas como API de análise/revisão.

### Variáveis e secrets

- `AGENT_REVIEW_LLM_ENABLED=true`
- `AGENT_ROUTER_BASE_URL=https://api.ks-sm.net:9443`
- `AGENT_ROUTER_API_KEY`
- `AGENT_ROUTER_MODEL` opcional

### Como configurar

1. Abra `Settings -> Secrets and variables -> Actions` no repositório.
2. Adicione `AGENT_ROUTER_API_KEY` como Secret.
3. Adicione `AGENT_REVIEW_LLM_ENABLED` como Variable com valor `true`.
4. Adicione `AGENT_ROUTER_BASE_URL` como Variable se quiser sobrescrever o padrão.
5. Adicione `AGENT_ROUTER_MODEL` como Variable se quiser fixar um modelo.

### Segurança do LLM

- O token do GitHub nunca é enviado ao Agent Router.
- O `AGENT_ROUTER_API_KEY` fica apenas no header `Authorization` da chamada ao router.
- O payload enviado ao router é sanitizado e truncado.
- O review nunca envia `env`, logs completos, secrets ou arquivos inteiros grandes.
- Se o router falhar, o workflow publica apenas o review determinístico e avisa que o LLM ficou indisponível.

### Garantias

- O workflow não executa código do PR.
- O workflow não faz `pull_request_target`.
- O workflow não usa `docker exec`, SSH ou deploy.
- O review final usa um marcador HTML estável para atualizar o comentário anterior e evitar spam.
- O comentário final é curto, prioriza P1/P2 e limita achados a no máximo 5.

## Exemplo de saída boa

- `P1`: `workflow usa pull_request_target com checkout/run`
- `P2`: `tests/test_action_run.py usa /opt/aiops-orchestrator hardcoded`
- `P3`: `docs podem explicar melhor o contrato`

## Troubleshooting

- Se `/agent review llm` publicar só o review determinístico, verifique `AGENT_REVIEW_LLM_ENABLED` e `AGENT_ROUTER_API_KEY`.
- Se houver `401` ou `403`, confirme o secret `AGENT_ROUTER_API_KEY`.
- Se houver `429`, o router está limitando a taxa e o fallback determinístico continua seguro.
- Se houver timeout ou falha de DNS/TLS, o review determinístico segue normalmente.
- Se o comentário anterior não atualizar, verifique se o bot tem permissão de `issues: write` e se o comentário contém o marcador HTML estável.

## Como testar depois do merge

1. Abra um PR de teste na `main`.
2. Comente `/agent review`.
3. Comente `/agent review llm`.
4. Observe se o comentário do bot é atualizado em vez de criar duplicatas.
5. Verifique no Actions se o workflow `agent-review` executou sem chamar código do PR.

## Limitações

- O agent não faz merge.
- O agent não faz push.
- O agent não executa scripts do PR.
- O agent não faz SSH nem usa `docker exec`.
- O agent nunca opera o CT 102.
