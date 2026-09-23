# Ciclo de PR — reconciliação portátil

**Owner:** MPACK; síntese operacional a adotar localmente. A sequência APCP v2 de origem é candidata e inclui governança específica de SACR-AS. Sua ordem e sua separação de planos são preservadas abaixo; nomes de checks, revisores, proteção de branch e quotas não são universalizados. [S01, S06]

| Fase APCP | Trabalho | Fronteira portátil |
|---|---|---|
| 0 | Declarar boundary contract | Pergunta, autoridade, subject, claims, perdas, owner, risco e stop |
| 1 | Construção e controles locais | Um reparo lógico por vez; comparar obrigações; controles causais |
| 2 | Selar successor e despachar | Registrar base/HEAD/tree; push somente quando autorizado |
| 3 | Reconciliar metadata/non-claims | Corpo e título devem descrever o candidate, não o predecessor |
| 4 | Qualificação remota em Draft | Percorrer gates requeridos pelo projeto e evidência adicional aplicável |
| 5 | Conferir política da plataforma | Plano de governança; observação real da proteção/rulesets, não inferência pelo YAML |
| 6 | Marcar Ready | Ação protegida conforme contrato local; não inferida de verde |
| 7 | Adjudicação governada | Canais separados não provam independência epistemológica automaticamente |
| 8 | Resolver findings | Resposta, disposição, reparo e requalificação do subject afetado |
| 9 | Guard de merge | HEAD/base esperados, política vigente, grant humano e risco residual |
| 10 | Read-back pós-merge | Integração realmente ocorrida e saúde posterior; não substitui gates prévios |

## Onde termina o método AOCM

`READY_FOR_HUMAN_DECISION` é disposição delimitada, não permissão para executar as fases protegidas. O MPACK fornece instruções para todas as fases, **mas sua ferramenta não executa Ready, revisão remota, merge ou deploy**. `CLEAN(S)` do PCM não é calculado por este pacote e não é apresentado como resultado upstream.

## Antes de alterar

Fixe fontes normativas e estado observado em planos separados. Não use um corpo de PR desatualizado como substituto do HEAD. Declare os gates requeridos antes de olhar seus resultados; não os retire depois de uma falha para produzir verde. Lacunas de política seguem para o owner.

## Depois de cada reparo

```
Patch → evolução incremental → evolução acumulada vs baseline
      → censo de consumidores → negativo focal → positivo
      → aceitar ou rejeitar a transação lógica
```

“Transação” aqui é disciplina de engenharia, não promessa de atomicidade Git/filesystem. Não continue empilhando reparos sobre uma transação rejeitada. [S06, S07]

## Fechamento e governança

Resultados ficam em observações externas ao subject congelado. Um relatório incorporado num commit futuro continua qualificando o subject que nomeia; não qualifica automaticamente o commit que passou a carregá-lo. Reviews de predecessor e checks de merge sintético não são renomeados como evidência source-head.

Registre o que foi observado, quais contratos foram realmente satisfeitos, limitações, findings adjudicados, ações protegidas não executadas e decisão seguinte. Semântica de unknown/indisponível é preservada.

Ao observar política antes e depois do merge, a igualdade dos endpoints não prova ausência de transições intermediárias. Capacidade real da plataforma deve ser verificada pelo projeto; o pacote não promete uma operação atômica HEAD+base+policy. [S06 §8]
