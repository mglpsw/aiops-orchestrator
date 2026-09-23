# Exercício sintético: o recibo que concorda consigo mesmo

Um produtor publica expected=`{a,b}` e um arquivo chamado observation contendo o mesmo campo copiado. O hash está correto e o JSON é válido. Isso prova transporte/consistência local; não prova que `{a,b}` corresponde à fonte material.

O protocolo deve separar a missão congelada, a fonte admitida e o evento de observação. Uma observação independente que encontrou `{a,c}` é um dado válido que pode refutar a representação; não deve ser rejeitada só por discordar. Um operando malformado é recusado antes do comparator. Uma observação indisponível bloqueia, sem refutar por default.

Controle positivo: representação sustentada pela fonte e evento adequado. Negativo: autoprojeção tratada como observação independente. Controle de não sobre-rejeição: uma observação válida que discorda deve permanecer admissível como evidência negativa.

Exemplo inspirado na separação Q1-0/Q1-1 [S05]. Nenhuma execução Q1 é afirmada.
