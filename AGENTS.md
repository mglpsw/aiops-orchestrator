<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
body_provenance: historical_caem_2_1_projection
not_a_CAEM_3_0_F0_generated_view: true
The CAEM 3.0 F0 interface manifest produces only: contract-registry.json,
3 schemas, a Python catalog, and generated/CAEM_3_0_F0_INTERFACE.md — never
this file. This document is an AIOps-local operational guide, NOT generated
by or regenerated from CAEM 3.0 F0.
Norma pertence a mglpsw/caem, nunca a este repositório. authority_effect=none.
Single source of CAEM identity: config/caem/caem-3.0-f0.pin.json
CAEM interface: 3.0.0 F0, maturity=development_freeze, published=false
Interface manifest digest: sha256:6e2fdff772a16466b8af1934d03b4e29ec03aeae053ccb2bfa0b705f50ab48e5
Verify: ./.venv/bin/python scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check
CAEM 2.1.0 material (previously vendored here) is quarantined, read-only,
authority_effect=none: see .caem/quarantine/caem-2.1/.
-->

# AGENTS.md — AIOps-AgentReview operational guide

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
