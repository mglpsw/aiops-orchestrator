# Instruções de Engenharia do Workspace — Antigravity (AGY)

Antes de planejar ou modificar o AIOps (`aiops-orchestrator`), leia:
- `docs/engineering/CAEM_CORE.md` (eixos de execução, presets e princípios CAEM)
- `docs/engineering/PROJECT_OVERLAY.md` (readiness e fronteiras de produto)
- `docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md` (regras e limiares de STOP/REDESIGN)
- [Issue #46](https://github.com/mglpsw/aiops-orchestrator/issues/46) (autoridade canônica única do roadmap)
- `.aocm/ENTRYPOINT.md` (método local de engenharia AOCM-MPACK)
- `.aocm/ADOPTION.md` (registro de adoção metodológica)
- `.aocm/repository-profile.json` (perfil do repositório e matriz de autoridade)

## Diretrizes Operacionais
1. **Método Local**: Adote as diretrizes do AOCM-MPACK conforme declaradas em `.aocm/ADOPTION.md`.
2. **Seleção de Perfil**: Escolha o perfil aplicável da pasta `.aocm/vendor/profiles/` para a tarefa em execução.
3. **Precedência e Contratos**: Respeite os contratos normativos e owners de runtime do produto (`docs/engineering/PROJECT_OVERLAY.md`, issue #46 como autoridade canônica de roadmap; `docs/AGENT_REVIEW_V2_ROADMAP.md` permanece arquivado como snapshot histórico), a norma upstream CAEM (`config/caem/caem-3.0-f0.pin.json`) e os critérios de `STOP/REDESIGN` (`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`).
4. **Grants e Ações Protegidas**: Esta instrução não concede poderes adicionais. Ações protegidas (push, criação de PR remota, merge, Ready, release, tag, deploy, mutações CT102, ativação de required checks, alterações de infraestrutura/homelab) exigem grant humano explícito do mantenedor.
