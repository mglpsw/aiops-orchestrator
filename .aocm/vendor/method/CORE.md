# Core portátil — owner local MPACK

**Papel:** disciplina proposta por este pacote; não alteração normativa de AOCM, CAEM ou SACR-AS. Cada regra aponta para os itens MP-K do registro de conhecimento e suas fontes. A autoridade do produto é descoberta por domínio e adotada por seu mantenedor, nunca substituída pelo pacote.

## Leitura de um minuto

1. **Nomeie a pergunta e quem decide.** Identifique fonte da obrigação, owner, subject, escopo e ação autorizada. Estado vivo informa fatos; contratos e grants governam deveres. Código divergente não revoga contrato. [MP-K01]
2. **Fixe a identidade.** Separe base, HEAD, tree, artefato, ocorrência, missão, contexto e execução. Evidência pertence ao objeto realmente consumido. [MP-K02]
3. **Limite a claim.** Diga o que afirma, o que não afirma, domínio de validade, limitações e falsificador mais próximo. [MP-K03]
4. **Derive obrigações antes do patch.** Nomeie mecanismos e consumidores materiais. Use matriz apenas se houver múltiplas obrigações/relações relevantes. [MP-K04]
5. **Não confunda presença com uso.** Gate existente, execução terminada e contrato satisfeito são coisas distintas. Inclua obrigações aplicáveis sem gate automatizado. [MP-K05]
6. **Teste causalmente.** Controle positivo, contramodelo focal e, quando proporcional, ablação do mecanismo. Vermelho pelo motivo errado não discrimina a claim. [MP-K06]
7. **Corrija sem apagar deveres.** Compare predecessor e candidato, também contra a baseline; toda contração material exige autorização do owner. [MP-K07]
8. **Integridade antes de interpretação.** Não transforme evidência malformada, desconhecida ou indisponível em resultado positivo. Quem valida o recibo não necessariamente produziu ou julgou a observação. [MP-K08]
9. **Pare a expansão da prova.** Finding válido pode exigir correção, redução de claim, limitação, mudança de owner ou retirada do mecanismo. Não reconstrua uma plataforma para provar um diff pequeno. [MP-K09]
10. **Revisão descobre; adjudicação decide.** Use perguntas independentes quando discriminantes, não quotas arbitrárias de agentes. Mesma saída de vários agentes não cria autoridade. [MP-K10]
11. **Conserve conhecimento com papel explícito.** Fonte, visão, histórico, hipótese e observação têm owners e lifecycles separados. Aprendizado candidato não vira norma por ser exportado. [MP-K11]
12. **Encerre só o que demonstrou.** Evidências atuais e limitações precedem a disposição humana. Planejamento, implementação, qualificação, Ready, merge, release e deploy não se confundem. [MP-K12]

## Contrato mínimo para uma mudança pequena

```
Objetivo | Fonte/owner | Base/HEAD ou outro subject preciso
Claim + non-claims | Superfície alterada | Risco
Controle positivo + falso caso material | Evidência/limitações
Ação autorizada | Stop | Próxima decisão humana
```

Não instancie todos os templates por ritual. Uma correção tipográfica pode exigir apenas esse registro e revisão do diff. Já documentação que altera um predicado, uma obrigação, uma regra clínica ou um protocolo de merge não é “só docs” por extensão de arquivo.

## Três grafos; nenhum substitui os outros

`Derivação: autoridade → produtor → artefato`

`Consumo: artefato → admissão → consumidor → efeito`

`Justificação: claim → mecanismo → contramodelo → discriminador → limitação`

Uma identidade correta não demonstra relação correta; uma relação correta não demonstra que o consumidor a atravessou. [S01, S09]

## Ordem de leitura

`CORE → PR_LIFECYCLE → perfil aplicável → template necessário → SEMANTIC_CONTRACTS/CONVERGENCE quando material`.
A rastreabilidade e os limites estão em `../provenance/RECONCILIATION.md`.
