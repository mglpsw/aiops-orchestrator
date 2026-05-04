# Prompt para melhorar o GitHub Agent Review

Use este prompt no agent do repositório `mglpsw/aiops-orchestrator`.

---

Você está trabalhando no repositório `mglpsw/aiops-orchestrator`.

Objetivo:
Melhorar o GitHub Agent Review para reduzir falsos positivos, melhorar contexto e calibrar severidade, especialmente ao revisar PRs do `mglpsw/AgentEscala`.

Contexto:
O review da PR #231 do AgentEscala foi parcialmente útil, mas teve problemas:

1. Marcou `ShellIcon` como import não utilizado e bloqueante, porém o arquivo final usava `ShellIcon`.
2. Marcou `date` possivelmente undefined em `CoverageSummary` como crítico, embora fosse risco defensivo e não bug confirmado.
3. Falou em falta de garantias de testes apesar da PR/report comentar testes verdes.
4. A causa provável foi análise por diff truncado + prompt sem regras fortes de evidência.

Arquivos prováveis:
- `scripts/github_agent_review.py`
- `tests/test_github_agent_review.py`
- docs do GitHub agent/reviewer, se existirem

Antes de editar:
1. Leia `scripts/github_agent_review.py`.
2. Leia `tests/test_github_agent_review.py`.
3. Identifique onde o prompt do LLM é montado.
4. Identifique onde o bundle/contexto é montado.
5. Identifique onde severidade/status são calculados.
6. Não mexa em deploy, produção, secrets reais, SSH, Docker remoto ou workflows destrutivos.

## 1. Evidência e severidade

Implementar ou reforçar as categorias:

- P0: secret real, produção, comando destrutivo, perda de dados, bypass auth.
- P1: bug confirmado por arquivo final, check/teste falho ou contrato quebrado.
- P2: risco provável, mas não confirmado.
- P3: melhoria/refactor.
- INFO: observação.

Regras:
- LLM não pode promover hipótese para P1.
- Diff truncado não pode gerar P1 salvo se a parte visível contém prova suficiente.
- Import/variável não usada só pode ser P1/P2 se confirmado por lint/check ou arquivo final completo.
- Se PR body/checks reportam testes verdes, não escrever “sem garantias de testes”; escrever no máximo “não validei localmente”.
- Problemas com “possivelmente”, “talvez”, “pode ser” devem ir para riscos não confirmados, não bloqueadores.

## 2. Prompt do LLM

Atualizar o system prompt para conter:

- revisão em pt-BR;
- foco em bugs reais;
- não dar comentários genéricos;
- separar achados confirmados de riscos não confirmados;
- não bloquear por diff truncado;
- não marcar import não usado sem arquivo final/lint;
- não sugerir backend/migration em PR frontend-only sem evidência;
- não fingir execução de testes.

Formato desejado do comentário:

```md
## 🤖 Agent Review

### Veredito
approve | needs_review | request_changes

### Escopo entendido
...

### Achados confirmados
...

### Riscos não confirmados
...

### Sugestões
...

### Testes/checks observados
...

### O que NÃO deve mudar
...
```

## 3. Contexto para AgentEscala

Quando `GITHUB_REPOSITORY` ou repo alvo for `mglpsw/AgentEscala`, adicionar contexto fixo ao bundle/prompt:

- AgentEscala é sistema de escala médica.
- Calendário é interface operacional principal.
- Backend mantém regra canônica.
- Frontend agrupa visualmente.
- 10-22H é sempre independente.
- 12H DIA independente nunca some por causa de 24H.
- 24H ocupado cobre sua própria metade DIA/NOITE.
- Notificações consomem audit_events e não alteram regra da escala.
- CT102 é produção e não é staging.
- PR frontend-only não deve sugerir backend/migration sem bug real.

## 4. Bundle/contexto

Melhorar o bundle enviado ao LLM para incluir, quando possível:

- PR title/body completo;
- arquivos alterados;
- checks conhecidos;
- comentários recentes;
- patches;
- indicador explícito `truncated=true/false`;
- conteúdo final dos arquivos principais quando viável, principalmente se o LLM for avaliar import/uso de símbolo.

Se não for viável buscar arquivo final completo, o LLM deve receber instrução explícita:

> Não confirme import não utilizado ou call site ausente com base apenas em diff truncado.

## 5. Testes novos obrigatórios

Adicionar testes em `tests/test_github_agent_review.py` para garantir:

1. Um import aparentemente suspeito no diff não vira P1/P2 quando o contexto está truncado.
2. Se o arquivo final mostra uso do símbolo, não há achado de import não utilizado.
3. LLM retornando P1 com linguagem especulativa não deve promover status para `changes_requested`.
4. PR body/checks com testes verdes não deve gerar frase “sem garantias de testes”.
5. Comentário final contém seções:
   - Achados confirmados;
   - Riscos não confirmados;
   - Testes/checks observados.
6. Contexto AgentEscala é incluído quando repo alvo é `mglpsw/AgentEscala`.
7. Diff truncado obriga frase ou metadado de cautela.

## 6. Validação

Rodar:

```bash
python3 -m pytest tests/test_github_agent_review.py -q
python3 -m compileall scripts tests
git diff --check
```

Se houver CI próprio:

```bash
python3 -m pytest -q
```

## 7. Relatório final

No final, reporte:

1. Branch criada.
2. Arquivos alterados.
3. Causa raiz.
4. Mudanças no prompt.
5. Mudanças no bundle/contexto.
6. Mudanças na classificação de severidade.
7. Testes adicionados.
8. Resultado real dos testes.
9. O que NÃO mudou:
   - deploy;
   - produção;
   - secrets;
   - workflows destrutivos;
   - AgentEscala;
   - CT102.
