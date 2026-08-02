<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
Source: .caem/policy.json (vendored copy; the canonical generator/tooling
that produces it, policy/caem-policy.json + tooling/generate.py +
tooling/validate.py, is external and not committed to this repository —
a Codex review found the previous paths/digest below did not correspond
to anything in this checkout).
CAEM: 2.1.0
Policy-SHA256 (of the vendored .caem/policy.json above): 5f8d13688555633a7b26124c81f390bdcc4ddda2ccb40d4a45427b95cbcb9928
Regenerate/validate: run the external CAEM generator/validator against
.caem/policy.json; this repository has no local tooling/generate.py or
tooling/validate.py to invoke directly.
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
