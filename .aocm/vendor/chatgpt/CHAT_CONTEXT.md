# AOCM-MPACK — contexto de conversa

GENERATED_VIEW. Owner: arquivos listados abaixo. Não editar isoladamente. Snapshot 2026-09-23.

---

## Origem local: `method/CORE.md`

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


---

## Origem local: `method/PR_LIFECYCLE.md`

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


---

## Origem local: `method/CONVERGENCE.md`

# Convergência e evolução de obrigações

## Não transformar review numa fábrica de verificadores

`ValidFinding != MoreMechanism`. Um defeito pode pedir correção local; uma overclaim pode pedir claim menor; uma responsabilidade deslocada pode pedir mudança de owner; um mecanismo inadequado pode precisar ser removido. Uma limitação jamais pode declarar seguro aquilo que o sistema aceita incorretamente. [S01]

A parada para adjudicar a abstração é motivada por sinais materiais: responsabilidade semântica crescente; reparo gerando contraexemplo na mesma responsabilidade; interpretação parcial de semântica externa aberta; prova se tornando segundo produto. Contagem de findings sozinha não decide isso.

## Successor Obligation Preservation — transferência candidata de APCP v2

Cada obrigação material deve receber disposição justificada comparando predecessor/candidato:

`UNCHANGED | STRENGTHENED | SEMANTICALLY_EQUIVALENT | AUTHORIZED_CONTRACTION | AUTHORIZED_REPLACEMENT`.

`UNKNOWN` ou `SILENT_WEAKENING` impedem afirmar preservação. Compare proposição, truth-maker, carrier, aplicabilidade, predicado de fechamento, classe de evidência, consumidores e limitações. Contração e substituição precisam do owner competente. [S06 §6A, S07 D4]

Não reduza isto a procurar palavras-chave. A ferramenta desta distribuição **não** decide equivalência semântica. O template é suporte à adjudicação, não um oráculo universal.

## Contabilizar não é descarregar

`Routing != Discharge` e `OwnerBoundary != ObligationDeletion`. Uma obrigação enviada à governança de merge não desaparece: permanece com owner, interface, condição de entrada e evidência futura. Na origem SACR-AS o corpus registra uma divisão específica 17/3 de 20 cláusulas; esses números são witnesses locais e **não** constantes do pacote. [S07 §8]

## Revisão finita

O reviewer deve buscar escapes além do universo já trabalhado, não substituir o planejamento. Cada rodada tem pergunta, subject, papel, stop e disposição. “CLEAN” pode ser observação válida no domínio contratado; não há quota fixa de revisões nem necessidade de inventar findings.

Separe defeito real, falha de observação, invocação errada e falha pré-existente. Não esconda um achado material por ser emitido por lane advisory; adjudique-o. Não trate ausência de uma lane advisory como falha de produto, salvo obrigação explícita.

## Preservar a descoberta

Registre o falso caso, a obrigação, a resolução, a identidade do subject e a limitação do mecanismo. Aprendizados vão primeiro para registro candidato. Promover para perfil/core exige evidência e decisão do owner; exportar um arquivo não faz essa promoção. [S01, S08]


---

## Origem local: `method/SEMANTIC_CONTRACTS.md`

# Fronteiras semânticas e evidência

## F1: identidade, domínio e estado consumido

O contrato do módulo de decisão F1 distingue qualificação e autoridade e declara leitura única em estado de avaliação congelado: identidade validada = consumida = nomeada pelo resultado. Este pacote reutiliza a **pergunta de auditoria**, não implementa nem certifica o kernel F1. [S03]

Distinguir entidade, ocorrência, relação, snapshot, execução e capability impede que um identificador válido seja usado como prova de pertencimento ou permissão. Validade dos participantes não prova validade da relação. O domínio de aplicabilidade precisa estar fixado antes da avaliação material.

## Q1: seis objetos que não podem colapsar

`QualificationSubject`, `SourceUniverse`, `ClaimLedger`, `Mission`, `QualificationReceipt` e `Authority` têm papéis diferentes. Inventário, aquisição, cobertura semântica e cobertura da representação são eixos distintos. Uma fonte listada, mas não adquirida, não sustenta claim positiva de conteúdo. [S05]

A versão lida de Q1-0 define `validate_receipt_integrity`: identidades, shape, digests, aboutness, referências, coleções e projeções mecânicas do carrier. A referência `validate_future_receipt_binding` acrescenta semântica, mas é explicitamente `NON_AUTHORITATIVE_Q1_1_REFERENCE_PROTOTYPE`. **Nenhum dos dois é execução independente de Q1.** Não importar a segunda função como certificador universal. [S05]

Integridade precede a relação. Uma evidência malformada é recusada; não vira indisponibilidade inocente nem refutação válida. Uma observação indisponível pode bloquear uma decisão, mas não provar que o subject está errado. A força exata desses estados deve vir do contrato local.

`PerPlaneBinding != CrossPlaneEquality`: expected e observed devem estar vinculados a seu próprio contrato por claim. Não imponha igualdade de tipos/composições entre planos só porque hoje têm valores iguais. Conjuntos, sequências, multisets e grafos precisam de semântica declarada; duplicatas não devem ser apagadas para fabricar concordância.

## Contrato, observação e índice

O contrato diz como avaliar. A observação diz o que aconteceu. O índice aponta para uma observação. Não colocar um resultado passageiro no contrato congelado para tentar fazê-lo provar a si mesmo. Ao armazenar uma observação em um successor, preservar o subject anterior que ela avaliou. [S01, S05, S06]

## Evidence reuse: conservador nesta versão

Um novo HEAD invalida a qualificação exact-head anterior. Uma prova determinística de não impacto pode justificar reuso de **evidência comportamental específica**, se o contrato local admitir, sem converter identidade predecessora em identidade sucessora. Isso não pode estender observações LIVE por invariância de código. O pacote não possui avaliador automático de no-impact nem aceita reuso por padrão. [S01; lição candidata da campanha Q1]

## STATIC, LIVE e MIXED

STATIC vincula propriedades ao subject imutável. LIVE depende de estado observado fora dele. MIXED exige ambas as partes. `observed_at` e política de frescor devem ser materializados onde necessários. Um hash do checkout não prova disponibilidade do provider nem estado atual de proteção da forge. Timestamps diferentes não são automaticamente objetos de domínio diferentes. [S06, S08]

## Producer/consumer: não universalizar thin client

Quando um owner já prova uma conclusão de domínio e o consumidor é read-only nessa decisão, publicar resultado delimitado pode evitar a reexecução indevida dessa prova. Isso não remove validação de transporte, identidade, versão, frescor ou autorização do consumidor/writer. Um verificador independente cuja função é testar adequação das fontes **não** deve aceitar a autoprojeção do produtor como oracle. [S02, S05]

## Gates e aplicabilidade

`GateExists != GateRequired != GateSatisfied`. Um gate requerido deve ter terminado, observado o subject correto e satisfeito seu contrato. Coverage zero não vira revisão concluída. Um perfil pode exigir evidência local não automatizada pelo gate remoto; seu sucesso não dispensa essa obrigação. `N/A_WITH_RATIONALE` exige demonstração de inaplicabilidade; `LIMITED` ainda exige sua evidência delimitada. [S01, S06]

## Projeções da própria síntese

Perda permitida: cronologia incidental, tentativas superadas e detalhes de implementação não usados na claim. Perda proibida: owners, estados de maturidade, identidade das fontes, fronteira entre integridade e semântica, entre qualificação e autorização, e lacunas de aquisição. Veja `../provenance/RECONCILIATION.md`.


---

## Origem local: `provenance/RECONCILIATION.md`

# Reconciliação — 23/09/2026

## Subject e classificação desta entrega

Esta é uma **síntese e implementação local de bootstrap**, não extração byte-idêntica de todo o AOCM/CAEM/PCM. O universo da reconciliação é o conjunto de fontes S01–S11 e os doze itens MP-K. Não é um censo exaustivo dos três repositórios ou das conversas. Cada fonte informa cobertura de leitura; leituras parciais não foram convertidas em aquisição integral.

## Resoluções materiais

| Tensão | Resolução |
|---|---|
| AOCM local vs “pack universal” | Método portátil candidato; sem criar AOCM v2 ou tornar o core dono da semântica do produto |
| ACPC vs APCP | Preservar o nome encontrado: Adversarial PR Construction Protocol / APCP v2 |
| “CORE” vs maturidade upstream | MP-K classifica origem integrada/candidata; inclusão no pack não promove candidato a norma |
| PR #98: corpo vs HEAD | Metadata vivo nomeou `33a91d7...`; corpo ainda falava de `44d36fb...`. Leitura pinada do README atual, sem herdar suas antigas contagens/verdicts |
| Q1-0 vs Q1-1 | Freeze/integridade separados de observação/adjudicação independente. Prototype não é execução |
| PR #33: corpo vs HEAD | Metadata vivo nomeou `6c3b9b9...`; corpo ainda falava de `0a0bcb7...`. APCP tratado como candidato aberto, não CLEAN provado |
| PR #41 vs PR #33 | Handoff/corpus da primeira não qualifica a segunda; tabelas históricas mantêm data própria |
| Qualificação F1 relatada vs adquirida | Kernel integrado é fonte lida; recibos externos relatados pela representação não foram adquiridos/reexecutados |
| RK1 ACTIVE vs pacote instalável | Empacotamento independente; não implementa F2 nem pressupõe RK1 concluído |
| Consumidor fino vs reviewer independente | Consumidor de apresentação não refaz domínio; reviewer source-first não usa autoprojeção como oracle |
| D4/D5 vs ritual de patch | Preservar obrigações e controle causal; transação lógica, não nova engine de equivalência |
| Obrigação encaminhada vs fechada | Registrar owner e condição; roteamento não descarrega obrigação futura |
| Metadados íntegros vs semântica correta | Validador de bytes/shape não emite `CLEAN`, `SATISFIED` de PR nem autorização |
| Evidência imutável vs LIVE | Invariância de código não atualiza observação da forge/runtime |
| Resultados vs subject | Relatórios da entrega ficam fora de `pack/`; manifest congela o subject avaliado |
| Atualização da distribuição vs história | Versão nova recebe novas fontes e relatório; não reescreve recibos anteriores |

## Correções à proposta conversacional anterior

As operações `plan`, `classify`, `qualify`, `close`, `learn` e `update` eram uma visão de produto, não implementação existente. Esta entrega implementa somente `verify`, `install` e `doctor`; o restante é disciplina documental e prompts. Também não foi criado o repositório `mglpsw/aocm-pack`, nenhum workflow foi instalado e nenhum check virou required.

A tabela anterior de conceitos candidatos ao CORE não ratificou normas. Evidence reuse, RK1 resolver e obligation-evolution oracle permanecem com limites e owners originais. Não há automatização de julgamento semântico escondida no instalador.

## Source contract / perda na destilação

Perdas permitidas: cronologia incidental, detalhes de implementação fora das claims, contagens antigas, ids de threads não usados. Perdas proibidas: nome e papel da fonte; exact subject; maturidade; ownership; diferença entre integridade, semântica, qualificação e autoridade; lacunas de aquisição; implicações negativas das limitações.

Não republiquei os documentos privados integrais. `SOURCE_REGISTRY.json` fornece links pinados e hashes quando realmente disponíveis. Blob Git reportado não foi rotulado como SHA-256 de bytes recomputado. Os três anexos montados receberam SHA-256 recomputado localmente; o restante foi adquirido como texto do conector.

## Limitações que continuam

Não houve execução de F1, Q1, SACR-AS, browser, PostgreSQL, providers ou CI upstream. Não houve review independente desta síntese. Não houve teste prospectivo de melhora de PRs reais. A evidência executável desta entrega cobre o pacote e sua instalação nos casos declarados. A adequação semântica da síntese foi reconciliada pelo mesmo autor, não certificada por um segundo avaliador.

Conhecimentos locais ainda não commitados, relatados em outras conversas, não receberam pin inventado e não integram as regras afirmadas como upstream. Os repositórios podem avançar após o corte; revalide antes de operar.


---

## Origem local: `provenance/GAPS.md`

# Lacunas e decisões pendentes

1. **Qualificação semântica independente do MPACK:** não executada. Necessária antes de alegar validação externa do método ou adoção normativa ampla.
2. **Eficácia entre repositórios:** sem estudo prospectivo/controlado; exemplos conhecidos não constituem controle independente. Um piloto futuro deve declarar métricas, baseline, falhas e custo.
3. **Q1:** missão ainda pendente; não adquirir um recibo não significa que ele não existe. Nada nesta entrega é recibo Q1 ou conclusão sobre C1–C8.
4. **F1:** código lido, não reexecutado. A representação histórica separa integração e qualificação relatada; preservar essa distinção.
5. **RK1:** roadmap lido, não contrato terminal implementado. Sem resolver genérico, SQLite, F2, repin ou instalação normativa CAEM.
6. **APCP/PCM:** PR #33 aberta; corpus #41 não é método canônico nem prova transferível. Não calcular seu kernel a partir do verde do pack.
7. **Aquisição:** fontes selecionadas, algumas parciais; não há claim de corpus completo ou preservação de todos os bytes originais.
8. **Plataformas:** testes executados somente no ambiente do recibo. Windows/macOS permanecem não executados; código portátil pretendido não é qualificação cross-platform.
9. **Instalador:** sem atualização/remoção automática, origem assinada, defesa contra host privilegiado hostil ou transação global. Pasta/repo sem mutadores concorrentes são premissas.
10. **Autorização e licença:** adoção e publicação pública requerem decisão do owner. Exportação privada não torna os repositórios públicos nem altera suas licenças.


---

## Origem local: `prompts/START.md`

# Prompt — START

Leia `method/CORE.md`, `provenance/RECONCILIATION.md` e o perfil local do repositório. Identifique fontes atuais por domínio e confirme base/HEAD quando a tarefa depender disso. Trate este pack como síntese candidata adotável, não autoridade upstream. Não execute comandos do repositório ou ações protegidas sem autorização adequada. Para a solicitação abaixo, produza contrato mínimo, in/out, claims/non-claims, controles, limitações e próxima ação. Se houver conflito de contratos ou owner desconhecido, exponha a lacuna sem inventar política.

SOLICITAÇÃO: [preencher]
