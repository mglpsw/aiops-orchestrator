# Regras de severidade e evidência para GitHub Agent Review

## Classificação de severidade

### P0 — Crítico absoluto

Use P0 apenas para:

- secret real vazado;
- token/chave privada real no diff;
- comando destrutivo real em workflow/script;
- risco direto de produção;
- perda/corrupção de dados;
- bypass claro de autenticação/autorização;
- uso de `pull_request_target` com execução de código não confiável;
- operação que toca CT102/produção sem autorização.

P0 exige evidência concreta.

### P1 — Bloqueia ship

Use P1 apenas para bug confirmado por pelo menos uma destas evidências:

- arquivo final completo mostra o bug;
- teste/check falhou;
- contrato documentado foi quebrado;
- endpoint/handler ficou claramente incompatível;
- lint/build falha comprovadamente;
- regressão funcional clara e reproduzível.

Não use P1 para hipótese.

### P2 — Resolver antes de ship, se aplicável

Use P2 para:

- risco provável, mas ainda não confirmado;
- inconsistência de UX relevante;
- comportamento que parece frágil;
- ausência de teste em área importante;
- mudança grande em DOM/markup com risco de regressão;
- possível incompatibilidade com contrato, mas sem prova total.

P2 pode ser `needs_review`, mas não deve afirmar bug como certo sem evidência.

### P3 — Melhoria

Use P3 para:

- refactor;
- redução de duplicação;
- padronização de nomes;
- melhoria de testes;
- melhoria de UX não bloqueante;
- documentação.

### INFO — Observação

Use INFO para:

- nota de contexto;
- elogio;
- lembrete operacional;
- confirmação de escopo;
- sugestão futura.

## Estados de evidência

Todo achado deve ter um campo mental ou explícito de evidência.

### Confirmado

Existe prova no arquivo final, teste, check, log ou contrato.

Exemplo:

> `frontend/src/pages/x.jsx` linha X chama função inexistente.

### Provável

O diff sugere risco, mas falta arquivo completo, teste ou contexto.

Exemplo:

> O diff mostra uso de `date`, mas não foi possível confirmar todos os call sites.

### Não confirmado por diff truncado

O agent viu apenas parte do diff.

Regra:

> Se o contexto estiver truncado, o achado não pode ser P1/P0 salvo se a própria parte visível contiver prova suficiente.

### Pergunta

Use quando a decisão depende de produto/contrato.

### Sugestão

Use quando é melhoria de qualidade sem bug real.

## Regras anti-falso-positivo

1. Nunca marcar import não utilizado sem confirmar no arquivo final completo ou via lint.
2. Nunca marcar variável não usada sem confirmação por lint, TypeScript, ESLint ou arquivo final completo.
3. Nunca dizer que testes não rodaram se a PR body/checks reportam testes verdes. Diga: “não validei localmente”.
4. Nunca inferir falha de build por classe Tailwind truncada no diff.
5. Nunca usar “possivelmente” e classificar como P1.
6. Nunca marcar como bloqueante algo fora do escopo declarado sem bug real.
7. Nunca sugerir backend/migration em PR frontend-only sem evidência forte.
8. Nunca confundir comentário de documentação com comando executável.
9. Nunca bloquear por duplicação de constantes se o contrato/API foi preservado.
10. Nunca promover texto livre do LLM para P1 sem evidência estruturada.

## Template obrigatório do comentário

```md
## 🤖 Agent Review

### Veredito
approve | needs_review | request_changes

### Escopo entendido
Resumo curto do que a PR pretende mudar e do que ela declara fora de escopo.

### Achados confirmados
- P0/P1/P2 apenas com evidência.

### Riscos não confirmados
- Hipóteses, diff truncado, call sites não inspecionados.

### Sugestões
- Melhorias não bloqueantes.

### Testes/checks observados
- O que foi visto nos checks/PR body.
- O que não foi executado pelo agent deve ser declarado como não executado.

### O que NÃO deve mudar
- Itens críticos fora de escopo.
```

## Frase obrigatória quando não houver prova

Use:

> Não consegui confirmar isso no contexto disponível; trate como hipótese, não como bloqueador.

## Frase obrigatória quando o diff estiver truncado

Use:

> O diff/contexto disponível está truncado; não vou classificar isso como bloqueante sem validação no arquivo final ou nos checks.
