<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
Source: policy/caem-policy.json
CAEM: 2.1.0
Policy-SHA256: 9aa4949a0a9b865ae3b9a589fdf4dbadd1254cfad8c11f3db26efce10303ba60
Regenerate/validate: python tooling/generate.py && python tooling/validate.py
-->

# CLAUDE.md — AIOps-AgentReview sob CAEM v2.1.0

@docs/engineering/CAEM_CORE.md
@docs/engineering/PROJECT_OVERLAY.md
@docs/engineering/CURRENT_CHECKPOINT.md

## Preflight obrigatório

Antes de qualquer modificação:

1. leia os imports e instruções mais específicas;
2. identifique objetivo, issue/PR e menor slice funcional;
3. execute `git fetch --all --prune` quando houver remoto;
4. confirme repositório, branch, base, HEAD, worktree e alterações preexistentes;
5. consulte forge/runtime vivos conforme a tarefa;
6. derive superfícies, risco, gates e stop conditions;
7. identifique ações protegidas e grants existentes.

## Regras

- Uma PR tem um objetivo operacional e fora de escopo explícito.
- PR aprovada, slice encerrada, release, deploy e saúde são estados independentes.
- Use eixos de execução; presets são conveniência.
- Evidência é ligada a identity, gate e environment.
- Mudança de HEAD torna provas afetadas stale por padrão.
- DEV canônico não autoriza produção.
- Não avance para merge, deploy, release ou próxima fase sem autoridade específica.
- Comunicação final registra mudanças, não mudanças, identidades, gates, limitações, worktree e ações protegidas executadas/não executadas.
