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
