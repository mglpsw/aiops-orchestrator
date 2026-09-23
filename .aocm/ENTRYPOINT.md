# Ponto de Entrada Local — AOCM-MPACK

Este repositório adotou o **AOCM-MPACK 0.1.0-preview.1** como seu método local de engenharia.

- Decisão de adoção registrada em: [`.aocm/ADOPTION.md`](ADOPTION.md) (Status: `ADOPTED_BY_MAINTAINER`)
- Perfil e autoridades por domínio: [`.aocm/repository-profile.json`](repository-profile.json)
- Entrada do agente no pacote: [`.aocm/vendor/agents/ENTRYPOINT.md`](vendor/agents/ENTRYPOINT.md)
- Método núcleo: [`.aocm/vendor/method/CORE.md`](vendor/method/CORE.md)
- Ciclo de PR: [`.aocm/vendor/method/PR_LIFECYCLE.md`](vendor/method/PR_LIFECYCLE.md)

## Diretrizes de Uso

1. Antes de planejar ou modificar o repositório, consulte `.aocm/ADOPTION.md` e os perfis aplicáveis em `.aocm/vendor/profiles/`.
2. Para esta fase de adoção, aplicam-se proporcionalmente: `docs`, `method-evolution` e `knowledge-reconciliation`.
3. Respeite a precedência de domínio: contratos de runtime e de produto (`docs/engineering/PROJECT_OVERLAY.md`, issue #46 como autoridade canônica de roadmap; `docs/AGENT_REVIEW_V2_ROADMAP.md` permanece arquivado como snapshot histórico), a norma upstream CAEM (`config/caem/caem-3.0-f0.pin.json`) e o preflight estrutural / limiares de `STOP/REDESIGN` (`docs/engineering/STRUCTURAL_CHANGE_PREFLIGHT.md`).
4. Operações protegidas (push, PR remota, merge, release, tag, deploy, mutações CT102, ativação de required checks, providers reais) exigem autorização humana explícita do mantenedor.
