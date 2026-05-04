# AIOps Orchestrator — Contexto para melhoria do GitHub Agent Review

## Objetivo

Melhorar o reviewer automático de PRs do `aiops-orchestrator`, especialmente quando ele revisa PRs do `mglpsw/AgentEscala`.

O objetivo não é transformar o agent em aprovador autônomo de merge. O objetivo é torná-lo um reviewer mais confiável, com menos falsos positivos, melhor uso de contexto e classificação correta de severidade.

## Problema observado

Na PR #231 do AgentEscala, o comentário do AIOps reviewer foi parcialmente útil, mas apresentou falsos positivos e severidade exagerada.

Exemplos:

1. Marcou `ShellIcon` como import não utilizado e bloqueante.
   - Isso estava errado no arquivo final, pois `ShellIcon` era usado em `OrientationCard` e no modal de recorrência.
   - Causa provável: análise baseada em diff truncado ou trecho parcial.

2. Marcou como crítico/bloqueante a possibilidade de `date` ser `undefined` em `CoverageSummary`.
   - Era um risco defensivo válido, mas não bug confirmado.
   - No arquivo final, `CoverageSummary` recebia `date={date}` a partir de state inicializado.

3. Disse que havia “mudanças extensas sem garantias de compatibilidade de testes”.
   - A PR body e comentário posterior reportavam testes verdes.
   - Se o agent não validou localmente, deve dizer “não validei localmente”, não “sem garantias”.

## Diagnóstico

O problema principal não é apenas modelo. É a combinação de:

- contexto insuficiente;
- diff truncado;
- falta de leitura do arquivo final;
- prompt pouco rígido sobre evidência;
- severidade mal calibrada;
- ausência de separação entre bug confirmado e hipótese.

## Regra principal

Nunca transformar hipótese em bloqueador.

Todo achado deve ser classificado como:

- confirmado;
- provável;
- pergunta;
- sugestão.

Somente achados confirmados podem virar P0/P1.

## Papel do reviewer

O reviewer deve:

- encontrar bugs reais;
- preservar contratos críticos;
- reduzir regressões;
- alertar sobre riscos;
- ser conservador ao bloquear;
- explicar evidência;
- evitar comentários genéricos;
- nunca fingir que executou teste que não executou.

## O que ele não deve fazer

- Não deve aprovar merge automaticamente.
- Não deve executar deploy.
- Não deve tocar produção.
- Não deve criar secrets.
- Não deve usar CT102 como staging.
- Não deve bloquear PR com base em diff truncado.
- Não deve inventar bug sem evidência no arquivo final, teste ou contrato.
