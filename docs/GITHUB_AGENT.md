# GitHub Agent Review

O GitHub Agent Review responde a comentários em Pull Requests com três comandos:

- `/agent review` para revisão determinística
- `/agent review llm` para revisão determinística com apoio opcional do Agent Router
- `/agent ask <pergunta>` para follow-up curto sobre o PR

## Uso

Comente em um Pull Request uma linha que comece com um dos comandos acima.

O workflow lê o evento do GitHub, consulta metadados e diff via API e publica uma resposta no próprio PR.

## Autorização

O agent só executa quando o autor do comentário tem uma destas permissões:

- `OWNER`
- `MEMBER`
- `COLLABORATOR`
- ou está listado em `AGENT_ALLOWED_USERS`

`AGENT_ALLOWED_USERS` aceita uma lista separada por vírgulas em `Settings -> Secrets and variables -> Actions`.

Se a pessoa não estiver autorizada, o bot responde com uma mensagem curta em português e encerra.

## Revisão determinística

O review determinístico:

- não executa código do PR
- não faz checkout da branch do PR para execução
- não faz deploy
- não opera containers
- não chama o Agent Router

Ele prioriza achados P1 e P2, limita a saída a no máximo 5 achados principais e evita comentários longos.

### Interpretação de status

- `approved`: não há achados P1/P2 determinísticos e o LLM não trouxe achados estruturados P1/P2
- `needs_review`: há achados P2 determinísticos ou achados P2 estruturados do LLM
- `changes_requested`: há achados P1 determinísticos ou achados P1 estruturados do LLM

Achados soltos em texto livre do LLM não promovem o status automaticamente.

## Modo LLM

`/agent review llm` usa o Agent Router apenas como API de análise/revisão.

### Variáveis e secrets

- `AGENT_REVIEW_LLM_ENABLED=true`
- `AGENT_ROUTER_BASE_URL=https://api.ks-sm.net:9443`
- `AGENT_ROUTER_API_KEY`
- `AGENT_ROUTER_MODEL` opcional
- `AGENT_ROUTER_TIMEOUT_SECONDS` opcional, recomendado `60` para o caminho via Ollama

### Como configurar

1. Abra `Settings -> Secrets and variables -> Actions` no repositório.
2. Adicione `AGENT_ROUTER_API_KEY` como Secret.
3. Adicione `AGENT_REVIEW_LLM_ENABLED` como Variable com valor `true`.
4. Adicione `AGENT_ROUTER_BASE_URL` como Variable se quiser sobrescrever o padrão.
5. Adicione `AGENT_ROUTER_MODEL` como Variable se quiser fixar um modelo.
6. Adicione `AGENT_ROUTER_TIMEOUT_SECONDS` como Variable se quiser ajustar o timeout do router.

### Segurança do LLM

- O token do GitHub nunca é enviado ao Agent Router.
- O `AGENT_ROUTER_API_KEY` fica apenas no header `Authorization` da chamada ao router.
- O payload enviado ao router é sanitizado e truncado.
- O review nunca envia `env`, logs completos, secrets ou arquivos inteiros grandes.
- Se o router falhar, o workflow publica apenas o review determinístico e avisa que o LLM ficou indisponível.
- Se o token do GitHub não puder criar ou atualizar comentários no PR, a resposta é escrita no `GITHUB_STEP_SUMMARY` do workflow em vez de falhar.
- O timeout padrão do router é `60s`; aumente só se o caminho até o Ollama exigir mais latência.

### Formato esperado do LLM

O agente pede respostas curtas, objetivas e acionáveis em pt-BR.

Para a revisão, o formato ideal é JSON estruturado com:

- `severity`
- `file`
- `evidence`
- `risk`
- `recommendation`

Para o `/agent ask`, a resposta deve ser curta e baseada apenas no diff e no contexto sanitizado.

## Follow-up com `/agent ask`

Use quando quiser perguntar algo como:

```text
/agent ask explique esse achado
```

O payload enviado ao router inclui:

- a pergunta do usuário
- o título do PR
- a descrição truncada do PR
- a lista de arquivos alterados
- o último comentário do bot, se existir
- achados determinísticos recentes, quando disponíveis
- snippets e diff truncados

### Respostas públicas

- Por padrão, as respostas públicas do bot ficam em pt-BR.
- Se o usuário perguntar explicitamente em outro idioma, o modelo deve responder no idioma do usuário.
- O bot responde curto e evita texto genérico.
- Se o usuário perguntar se algo é falso positivo, o bot responde com base no diff/contexto e sinaliza incerteza quando existir.

### Fallbacks

- Se `AGENT_REVIEW_LLM_ENABLED=false` ou `AGENT_ROUTER_API_KEY` estiver ausente, o bot responde:
  - `Agent ask requer LLM habilitado; use /agent review para review determinístico.`
- Se o router falhar, o bot responde com um fallback curto em português e o workflow não quebra.
- Se o bot não puder comentar no PR, a resposta vai para `GITHUB_STEP_SUMMARY`.

## Garantias

- O workflow não executa código do PR.
- O workflow não faz `pull_request_target`.
- O workflow não usa `docker exec`, SSH ou deploy.
- O review final usa um marcador HTML estável para atualizar o comentário anterior e evitar spam.
- O comentário final é curto e prioriza P1/P2.

## Exemplo de saída

- `Status: approved`
- `Status: needs_review`
- `Status: changes_requested`
- `P1 — Bloqueadores`: execução perigosa ou risco de segurança
- `P2 — Importantes`: lacuna de cobertura, path hardcoded, timeout ausente
- `P3 — Sugestões`: melhorias de clareza e pequenos ajustes

## Troubleshooting

- Se `/agent review llm` publicar só o review determinístico, verifique `AGENT_REVIEW_LLM_ENABLED` e `AGENT_ROUTER_API_KEY`.
- Se houver `401` ou `403`, confirme o secret `AGENT_ROUTER_API_KEY`.
- Se houver `429`, o router está limitando a taxa e o fallback determinístico continua seguro.
- Se houver timeout, verifique o endpoint do router, o modelo configurado e a latência antes de aumentar `AGENT_ROUTER_TIMEOUT_SECONDS`.
- Se houver falha de DNS/TLS, o review determinístico segue normalmente.
- Se o comentário anterior não atualizar, verifique se o bot tem permissão de `issues: write` e se o comentário contém o marcador HTML estável.
- Se o bot não puder comentar no PR, procure a resposta no Step Summary da execução do workflow.

## Como testar depois do merge

1. Abra um PR de teste na `main`.
2. Comente `/agent review`.
3. Comente `/agent review llm`.
4. Comente `/agent ask explique esse achado`.
5. Observe se a resposta do bot fica em pt-BR e se o comentário é atualizado em vez de criar duplicatas para a revisão.
6. Verifique no Actions se o workflow `agent-review` executou sem chamar código do PR.

## Limitações

- O agent não faz merge.
- O agent não faz push.
- O agent não executa scripts do PR.
- O agent não faz SSH nem usa `docker exec`.
- O agent nunca opera o CT 102.
