<!-- GENERATED VIEW — DO NOT EDIT IN ISOLATION
Source: CAEM v2.1.0 policy + project overlay
Policy-SHA256: 9aa4949a0a9b865ae3b9a589fdf4dbadd1254cfad8c11f3db26efce10303ba60
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
