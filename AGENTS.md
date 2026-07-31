<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
Source: policy/caem-policy.json
CAEM: 2.1.0
Policy-SHA256: 9aa4949a0a9b865ae3b9a589fdf4dbadd1254cfad8c11f3db26efce10303ba60
Regenerate/validate: python tooling/generate.py && python tooling/validate.py
-->

# AGENTS.md — AIOps-AgentReview sob CAEM v2.1.0

Leia também:

- `docs/engineering/CAEM_CORE.md`;
- `docs/engineering/PROJECT_OVERLAY.md`;
- `docs/engineering/CURRENT_CHECKPOINT.md`.

## Preflight

Antes de editar: revalide repository/branch/base/HEAD/worktree, issue/PR, dependências, concorrência de escopo e estado vivo necessário.

## Contrato

Defina objetivo único, eixos/preset, escopo dentro/fora, superfícies, risco, gates, aceite, stop conditions e grants. Escolha a menor slice funcional.

## Execução

- preserve mudanças preexistentes;
- não faça alteração oportunista;
- use writers e contratos canônicos;
- preview/dry-run é write-zero;
- fail-closed para identidade, autorização, schema e coverage;
- selecione gates por impacto;
- classifique falhas antes de repetir;
- produza evidência por SHA/ambiente/gate;
- nunca fabrique prova.

## Autoridade

Merge, deploy, release, produção, providers reais, banco destrutivo, proxy/DNS e próxima fase exigem grants específicos. Handoff não transfere grants.

## Encerramento

Informe mudanças/não mudanças, base/HEAD/tested/deployed identities, gates, limitações, PR/worktree, ações protegidas executadas/não executadas e próxima ação mínima.
