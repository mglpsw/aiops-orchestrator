# Instruções de Engenharia do Workspace — Antigravity (AGY)

Antes de planejar ou modificar o AIOps (`aiops-orchestrator`), leia:
- `.aocm/ENTRYPOINT.md`
- `.aocm/ADOPTION.md`
- `.aocm/repository-profile.json`

## Diretrizes Operacionais
1. **Método Local**: Adote as diretrizes do AOCM-MPACK conforme declaradas em `.aocm/ADOPTION.md`.
2. **Seleção de Perfil**: Escolha o perfil aplicável da pasta `.aocm/vendor/profiles/` para a tarefa em execução.
3. **Precedência e Contratos**: Respeite os contratos normativos e owners de runtime do produto (`docs/engineering/PROJECT_OVERLAY.md`, `docs/AGENT_REVIEW_V2_ROADMAP.md`), a norma upstream CAEM (`config/caem/caem-3.0-f0.pin.json`) e os critérios de `STOP/REDESIGN` (`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`).
4. **Grants e Ações Protegidas**: Esta instrução não concede poderes adicionais. Ações protegidas (push, merge, Ready, release, deploy, alterações de infraestrutura/homelab) exigem grant humano explícito do mantenedor.
