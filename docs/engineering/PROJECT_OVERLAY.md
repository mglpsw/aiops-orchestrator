<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
body_provenance: historical_caem_2_1_projection
not_a_CAEM_3_0_F0_generated_view: true
The CAEM 3.0 F0 interface manifest produces only: contract-registry.json,
3 schemas, a Python catalog, and generated/CAEM_3_0_F0_INTERFACE.md — never
this file. This document's BODY is a preserved historical CAEM 2.1
operational overlay, NOT regenerated from or by CAEM 3.0 F0.
Norma pertence a mglpsw/caem, nunca a este repositório. authority_effect=none.
Single source of CAEM identity: config/caem/caem-3.0-f0.pin.json
CAEM interface: 3.0.0 F0, maturity=development_freeze, published=false
Interface manifest digest: sha256:6e2fdff772a16466b8af1934d03b4e29ec03aeae053ccb2bfa0b705f50ab48e5
Verify: ./.venv/bin/python scripts/verify-caem-f0-pin.py --pin config/caem/caem-3.0-f0.pin.json --check
CAEM 2.1.0 material (previously vendored here) is quarantined, read-only,
authority_effect=none: see .caem/quarantine/caem-2.1/.
-->

# Overlay — AIOps e AgentReview

## Papel

AIOps e AgentReview ampliam observação e revisão, sem substituir CI, autorização humana ou executores determinísticos.

## Arquitetura de confiança

```text
produto alvo: diff/checks/evidência
→ orquestrador: intake/redaction/context/chunking/parser/síntese
→ Router: transporte de inferência
→ runner autorizado: checkout/tooling temporário
→ quality/readiness gate determinístico
→ publisher: revalida PR/HEAD e publica quando autorizado
```

## Identidade mínima de uma execução

- repository;
- PR/change request;
- base SHA;
- head SHA;
- tested/synthetic merge SHA;
- tool repository SHA;
- profile/policy/manifest hashes;
- evidence bundle hash.

Branch ou tag móvel não é identidade suficiente.

## Readiness

Estados canônicos:

```text
ready
blocked_code
blocked_pipeline
manual_required
stale
```

`ready` requer, conforme política:

- mesmo HEAD;
- checks aplicáveis;
- coverage completa das superfícies críticas;
- nenhum finding acionável de severidade bloqueante;
- schema/reason codes válidos;
- evidência íntegra e sanitizada.

Review textual do modelo é advisory. `review-readiness` determinístico é a decisão consumível.

## Fronteiras

- CI determinístico permanece autoridade sobre testes.
- CT104 pode ser runner/tooling; CT102 nunca é runner/staging.
- acesso HTTPS ao Router não autoriza host, SSH, Docker ou filesystem.
- job de análise não ganha permissão de escrita/publicação.
- publisher não altera código, deploy ou infraestrutura.
- não há auto-merge, auto-deploy ou remediação de produção por LLM.
- infraestrutura material não recebe aprovação “com follow-up”.

## AIOps semanal

A coleta pode marcar janelas críticas, agregar métricas/logs sanitizados e gerar review periódico de bugs, gargalos e hipóteses. Recomendações devem incluir evidência, confiança, contra-hipóteses e próxima investigação. Ação corretiva abre fluxo separado com task contract e grant.
