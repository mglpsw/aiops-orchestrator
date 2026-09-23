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
