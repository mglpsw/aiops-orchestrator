# Guia humano — da intenção ao fechamento

## Em 30 segundos

O pacote faz o agente responder três perguntas antes de programar: **o que precisa ficar verdadeiro, quem definiu isso e como saberemos que ficou verdadeiro?** Ao final, ele deve mostrar evidência desse resultado e dizer o que permanece fora da demonstração.

Não promete eliminar todos os defeitos. Promete uma disciplina que torna mais visíveis as afirmações excessivas, as lacunas e as decisões que ainda precisam de um humano.

## Em cinco minutos

Pense numa PR que altera uma seleção de horários. Um teste mostrar o botão não prova que os horários oferecidos são permitidos. Um dado válido não prova que pertence à data selecionada. Um endpoint funcionar não prova que todos os consumidores o usam. Um CI verde não prova que executou os testes necessários.

O trabalho começa reduzindo a pergunta: “neste caminho e nesta versão, a interface preserva o alvo emitido pelo servidor e o escritor rejeita estado desatualizado sem persistir alterações”. Agora existem uma fronteira, um efeito observável e um falso caso claro. Não é necessário provar tudo sobre calendários para provar isso. Esse é um exemplo didático, não uma afirmação sobre o estado de qualquer PR.

**Claim** é uma afirmação verificável. **Truth-maker** é a fonte ou mecanismo que a torna verdadeira. **Subject** é o objeto exato examinado. **Contramodelo** é um caso no qual a afirmação falha. **Qualificação** é a avaliação delimitada; **autorização** é a decisão de permitir uma ação.

## O ciclo de trabalho

Comece pelo contrato. Faça a menor mudança funcional. Teste um caso permitido e um caso que deve ser recusado. Confira os consumidores e as obrigações anteriores. Quando o defeito for local, corrija localmente. Quando o verificador começar a imitar todo o domínio, reavalie a pergunta e o owner.

A revisão final não precisa inventar novos defeitos para ser útil. Ela deve buscar escapes materiais no subject exato, registrar achados e permitir um encerramento finito quando o domínio declarado estiver suficientemente coberto.

## O que cada projeto acrescentou

AOCM contribui com fechamento operacional proporcional. APCP v2 acrescenta preservação das obrigações durante a mudança. F1 distingue identidades, relações, qualificação e autoridade. Q1 explicita que congelar a pergunta e validar o recibo não é executar uma revisão independente das fontes. RK-1 mostra por que uma descoberta precisa de owner, lifecycle e rota de recuperação para não voltar a depender da memória. As maturidades diferentes dessas fontes estão no registro de proveniência. [S01, S03–S08]

## Como saber que não estamos só preenchendo formulários

Pergunte: “qual decisão material este campo melhora?” Se não houver resposta, registre N/A justificado ou simplifique. Pergunte também: “o teste falharia se o mecanismo importante desaparecesse?” Se a resposta não foi investigada, não diga que uma contagem de testes prova o mecanismo.

Por último: um relatório bem formatado continua sendo um relatório. Sem acesso à fonte ou à execução necessária, o resultado é limitado, pendente ou bloqueado — nunca aprovado por aparência.
