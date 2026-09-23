# Exercício sintético: PR de normalização de identificador

**DIDACTIC_ONLY.** Não é replay de PR upstream nem evidência independente de eficácia do método.

## Pequena mudança

Produto hipotético possui contrato: identificadores ASCII case-insensitive, sem whitespace e com unicidade por organização. Uma PR deve converter para minúsculas na entrada, sem mudar autorização.

Claim delimitada: o caminho de criação mantém a unicidade por `(organization_id, normalized_id)` conforme contrato. Non-claim: segurança universal ou migração dos dados existentes.

A matriz deve distinguir normalização, identidade composta e constraint de persistência. Um teste positivo aceita IDs diferentes em organizações diferentes. Um negativo tenta `AbC` e `abc` na mesma organização. Uma corrida só entra no perfil se a claim prometer comportamento concorrente; nesse caso, teste o banco e não apenas um mock.

## Evolução

Um reparo que elimina `organization_id` para simplificar a constraint corrige um caso, mas viola o positivo entre organizações. D4 detecta mudança de domínio; D5 exige negativo e positivo antes de aceitar a transação lógica.

## Fechamento

Se só o normalizador foi testado, a claim permitida é sobre normalização, não sobre persistência concorrente. Nenhum agente pode chamar a PR inteira de qualificada usando apenas o receipt de integridade do pack.
