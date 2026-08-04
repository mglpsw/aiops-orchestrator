<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
Norma pertence a mglpsw/caem, nunca a este repositório. Este documento é
projeção operacional local, authority_effect=none.
Single source of CAEM identity: config/caem/caem-3.0-f0.pin.json
CAEM interface: 3.0.0 F0, maturity=development_freeze, published=false
Interface manifest digest: sha256:6e2fdff772a16466b8af1934d03b4e29ec03aeae053ccb2bfa0b705f50ab48e5
Verify: ./.venv/bin/python scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check
CAEM 2.1.0 material (previously vendored here) is quarantined, read-only,
authority_effect=none: see .caem/quarantine/caem-2.1/.
-->

# CLAUDE.md — AIOps-AgentReview sob CAEM 3.0 F0 (development_freeze)

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
